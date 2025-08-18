"""
Cryptography utilities for Ashy Terminal.

This module provides secure encryption/decryption for sensitive data like passwords,
using industry-standard cryptographic practices with proper key derivation and secure storage.
"""

import os
import base64
import hashlib
import secrets
from typing import Optional, Union, Tuple, Dict, Any
from pathlib import Path
import json

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    from cryptography.hazmat.backends import default_backend
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

from .logger import get_logger
from .exceptions import (
    AshyTerminalError, ErrorCategory, ErrorSeverity,
    ConfigError, StorageError, PermissionError
)


class CryptographyNotAvailableError(ConfigError):
    """Raised when cryptography library is not available."""
    
    def __init__(self, **kwargs):
        message = "Cryptography library is not available"
        kwargs.setdefault('severity', ErrorSeverity.CRITICAL)
        kwargs.setdefault('user_message', 
                         "Security features require cryptography library. Please install python3-cryptography")
        super().__init__(message, **kwargs)


class EncryptionError(AshyTerminalError):
    """Raised when encryption/decryption operations fail."""
    
    def __init__(self, operation: str, reason: str, **kwargs):
        message = f"Encryption {operation} failed: {reason}"
        kwargs.setdefault('category', ErrorCategory.SYSTEM)
        kwargs.setdefault('severity', ErrorSeverity.HIGH)
        kwargs.setdefault('details', {'operation': operation, 'reason': reason})
        kwargs.setdefault('user_message', "Security operation failed")
        super().__init__(message, **kwargs)


class KeyDerivationMethod:
    """Key derivation methods."""
    PBKDF2 = "pbkdf2"
    SCRYPT = "scrypt"


class SecureStorage:
    """Secure storage for encrypted data with proper key management."""
    
    def __init__(self, storage_dir: Optional[Path] = None):
        """
        Initialize secure storage.
        
        Args:
            storage_dir: Directory for storing encrypted data
        """
        if not CRYPTO_AVAILABLE:
            raise CryptographyNotAvailableError()
        
        self.logger = get_logger('ashyterm.crypto')
        
        # Set up storage directory
        if storage_dir is None:
            storage_dir = Path.home() / ".config" / "ashyterm" / "secure"
        
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        
        # Key and salt files
        self.key_file = self.storage_dir / "master.key"
        self.salt_file = self.storage_dir / "master.salt"
        self.config_file = self.storage_dir / "crypto.json"
        
        # Ensure proper permissions
        self._ensure_secure_permissions()
        
        # Load or create master key
        self._master_key: Optional[bytes] = None
        self._fernet: Optional[Fernet] = None
        
    def _ensure_secure_permissions(self):
        """Ensure secure file permissions on storage directory."""
        try:
            # Set directory permissions to 700 (owner only)
            self.storage_dir.chmod(0o700)
            
            # Set file permissions to 600 for existing files
            for file_path in [self.key_file, self.salt_file, self.config_file]:
                if file_path.exists():
                    file_path.chmod(0o600)
                    
        except OSError as e:
            raise PermissionError(
                directory_path=str(self.storage_dir),
                operation="set permissions",
                details={'reason': str(e)}
            )
    
    def _generate_salt(self, length: int = 32) -> bytes:
        """Generate cryptographically secure salt."""
        return secrets.token_bytes(length)
    
    def _derive_key_pbkdf2(self, password: bytes, salt: bytes, iterations: int = 100000) -> bytes:
        """Derive key using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
            backend=default_backend()
        )
        return kdf.derive(password)
    
    def _derive_key_scrypt(self, password: bytes, salt: bytes, 
                          n: int = 16384, r: int = 8, p: int = 1) -> bytes:
        """Derive key using Scrypt (more secure but slower)."""
        kdf = Scrypt(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            n=n,
            r=r,
            p=p,
            backend=default_backend()
        )
        return kdf.derive(password)
    
    def _create_master_key(self, passphrase: Optional[str] = None, 
                          method: str = KeyDerivationMethod.SCRYPT) -> bytes:
        """
        Create a new master key.
        
        Args:
            passphrase: Optional passphrase for key derivation
            method: Key derivation method
            
        Returns:
            Master key bytes
        """
        try:
            salt = self._generate_salt()
            
            if passphrase:
                # Derive key from passphrase
                password_bytes = passphrase.encode('utf-8')
                
                if method == KeyDerivationMethod.SCRYPT:
                    master_key = self._derive_key_scrypt(password_bytes, salt)
                    config_data = {
                        'method': method,
                        'n': 16384,
                        'r': 8,
                        'p': 1,
                        'iterations': None
                    }
                else:  # PBKDF2
                    iterations = 100000
                    master_key = self._derive_key_pbkdf2(password_bytes, salt, iterations)
                    config_data = {
                        'method': method,
                        'iterations': iterations,
                        'n': None,
                        'r': None,
                        'p': None
                    }
            else:
                # Generate random key (stored securely)
                master_key = secrets.token_bytes(32)
                config_data = {
                    'method': 'random',
                    'iterations': None,
                    'n': None,
                    'r': None,
                    'p': None
                }
            
            # Save salt and config
            with open(self.salt_file, 'wb') as f:
                f.write(salt)
            self.salt_file.chmod(0o600)
            
            with open(self.config_file, 'w') as f:
                json.dump(config_data, f)
            self.config_file.chmod(0o600)
            
            # Save encrypted master key if using passphrase
            if passphrase:
                # For passphrase-derived keys, we don't store the key itself
                self.logger.info("Created passphrase-derived master key")
            else:
                # For random keys, store encrypted with machine-specific data
                machine_key = self._get_machine_key()
                fernet_machine = Fernet(base64.urlsafe_b64encode(machine_key))
                encrypted_key = fernet_machine.encrypt(master_key)
                
                with open(self.key_file, 'wb') as f:
                    f.write(encrypted_key)
                self.key_file.chmod(0o600)
                
                self.logger.info("Created random master key with machine encryption")
            
            return master_key
            
        except Exception as e:
            self.logger.error(f"Failed to create master key: {e}")
            raise EncryptionError("key creation", str(e))
    
    def _get_machine_key(self) -> bytes:
        """
        Generate a machine-specific key for encrypting stored keys.
        This provides basic protection but is not foolproof.
        """
        # Combine various machine-specific data
        import platform
        import socket
        
        machine_data = f"{platform.node()}{platform.machine()}{platform.platform()}"
        
        try:
            # Add network interface info if available
            hostname = socket.gethostname()
            machine_data += hostname
        except:
            pass
        
        # Hash to get consistent 32-byte key
        return hashlib.sha256(machine_data.encode('utf-8')).digest()
    
    def _load_master_key(self, passphrase: Optional[str] = None) -> Optional[bytes]:
        """
        Load existing master key.
        
        Args:
            passphrase: Passphrase if key was derived from one
            
        Returns:
            Master key bytes or None if not found
        """
        try:
            if not self.config_file.exists() or not self.salt_file.exists():
                self.logger.debug("Master key files not found - first run or missing keys")
                return None
            
            # Load configuration
            with open(self.config_file, 'r') as f:
                config = json.load(f)
            
            # Load salt
            with open(self.salt_file, 'rb') as f:
                salt = f.read()
            
            method = config.get('method')
            
            if method == 'random':
                # Load encrypted key
                if not self.key_file.exists():
                    return None
                
                with open(self.key_file, 'rb') as f:
                    encrypted_key = f.read()
                
                # Decrypt with machine key
                machine_key = self._get_machine_key()
                fernet_machine = Fernet(base64.urlsafe_b64encode(machine_key))
                master_key = fernet_machine.decrypt(encrypted_key)
                
                self.logger.debug("Loaded random master key")
                return master_key
                
            elif method in [KeyDerivationMethod.PBKDF2, KeyDerivationMethod.SCRYPT]:
                if not passphrase:
                    raise EncryptionError("key loading", "Passphrase required for derived key")
                
                password_bytes = passphrase.encode('utf-8')
                
                if method == KeyDerivationMethod.SCRYPT:
                    master_key = self._derive_key_scrypt(
                        password_bytes, salt,
                        config.get('n', 16384),
                        config.get('r', 8),
                        config.get('p', 1)
                    )
                else:  # PBKDF2
                    master_key = self._derive_key_pbkdf2(
                        password_bytes, salt,
                        config.get('iterations', 100000)
                    )
                
                self.logger.debug(f"Loaded {method} derived master key")
                return master_key
            
            else:
                raise EncryptionError("key loading", f"Unknown key derivation method: {method}")
                
        except Exception as e:
            self.logger.error(f"Failed to load master key: {e}")
            if "passphrase required" in str(e).lower():
                raise
            raise EncryptionError("key loading", str(e))
    
    def initialize(self, passphrase: Optional[str] = None, 
              force_create: bool = False) -> bool:
        """
        Initialize secure storage with master key.
        
        Args:
            passphrase: Optional passphrase for key derivation
            force_create: Force creation of new key even if one exists
            
        Returns:
            True if initialization successful
        """
        try:
            self.logger.debug(f"Initializing secure storage - force_create: {force_create}")
            has_key = self._has_master_key()
            self.logger.debug(f"Has existing master key: {has_key}")
            
            if force_create or not has_key:
                self.logger.debug("Creating new master key")
                self._master_key = self._create_master_key(passphrase)
            else:
                self.logger.debug("Loading existing master key")
                try:
                    self._master_key = self._load_master_key(passphrase)
                except Exception as e:
                    from cryptography.fernet import InvalidToken
                    if isinstance(e, InvalidToken):
                        self.logger.warning("Corrupted master key detected, regenerating...")
                        # Remove corrupted files
                        for file_path in [self.key_file, self.salt_file, self.config_file]:
                            if file_path.exists():
                                file_path.unlink()
                        # Create new key
                        self._master_key = self._create_master_key(passphrase)
                    else:
                        raise
            
            if self._master_key:
                # Create Fernet instance
                key_b64 = base64.urlsafe_b64encode(self._master_key)
                self._fernet = Fernet(key_b64)
                self.logger.info("Secure storage initialized successfully")
                return True
            
            self.logger.warning("Failed to initialize secure storage - no master key available")
            return False
            
        except Exception as e:
            error_msg = str(e) if str(e) else f"Unknown error of type {type(e).__name__}"
            self.logger.error(f"Failed to initialize secure storage: {error_msg}")
            raise
    
    def _has_master_key(self) -> bool:
        """Check if master key exists."""
        return (self.config_file.exists() and self.salt_file.exists() and
                (self.key_file.exists() or self._is_passphrase_derived()))
    
    def _is_passphrase_derived(self) -> bool:
        """Check if key is derived from passphrase."""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                return config.get('method') in [KeyDerivationMethod.PBKDF2, KeyDerivationMethod.SCRYPT]
        except:
            pass
        return False
    
    def encrypt(self, data: Union[str, bytes]) -> str:
        """
        Encrypt data.
        
        Args:
            data: Data to encrypt (string or bytes)
            
        Returns:
            Base64-encoded encrypted data
        """
        if not self._fernet:
            raise EncryptionError("encryption", "Secure storage not initialized")
        
        try:
            if isinstance(data, str):
                data = data.encode('utf-8')
            
            encrypted = self._fernet.encrypt(data)
            return base64.urlsafe_b64encode(encrypted).decode('ascii')
            
        except Exception as e:
            self.logger.error(f"Encryption failed: {e}")
            raise EncryptionError("encryption", str(e))
    
    def decrypt(self, encrypted_data: str) -> str:
        """
        Decrypt data.
        
        Args:
            encrypted_data: Base64-encoded encrypted data
            
        Returns:
            Decrypted string
        """
        if not self._fernet:
            raise EncryptionError("decryption", "Secure storage not initialized")
        
        try:
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode('ascii'))
            decrypted = self._fernet.decrypt(encrypted_bytes)
            return decrypted.decode('utf-8')
            
        except Exception as e:
            self.logger.error(f"Decryption failed: {e}")
            raise EncryptionError("decryption", str(e))
    
    def encrypt_dict(self, data: Dict[str, Any]) -> str:
        """
        Encrypt a dictionary.
        
        Args:
            data: Dictionary to encrypt
            
        Returns:
            Base64-encoded encrypted JSON
        """
        json_str = json.dumps(data, ensure_ascii=False)
        return self.encrypt(json_str)
    
    def decrypt_dict(self, encrypted_data: str) -> Dict[str, Any]:
        """
        Decrypt a dictionary.
        
        Args:
            encrypted_data: Base64-encoded encrypted JSON
            
        Returns:
            Decrypted dictionary
        """
        json_str = self.decrypt(encrypted_data)
        return json.loads(json_str)
    
    def is_initialized(self) -> bool:
        """Check if secure storage is initialized and ready."""
        return self._fernet is not None
    
    def change_passphrase(self, old_passphrase: Optional[str], 
                         new_passphrase: Optional[str]) -> bool:
        """
        Change the passphrase for the master key.
        
        Args:
            old_passphrase: Current passphrase (None if using random key)
            new_passphrase: New passphrase (None to switch to random key)
            
        Returns:
            True if change successful
        """
        try:
            # Load current master key
            if not self.initialize(old_passphrase):
                raise EncryptionError("passphrase change", "Failed to load current key")
            
            current_master_key = self._master_key
            
            # Remove old key files
            for file_path in [self.key_file, self.salt_file, self.config_file]:
                if file_path.exists():
                    file_path.unlink()
            
            # Create new key with new passphrase
            self._master_key = self._create_master_key(new_passphrase)
            
            # Verify the key was created correctly
            if not self._master_key:
                # Restore old key if creation failed
                self._master_key = current_master_key
                raise EncryptionError("passphrase change", "Failed to create new key")
            
            # Re-initialize with new key
            key_b64 = base64.urlsafe_b64encode(self._master_key)
            self._fernet = Fernet(key_b64)
            
            self.logger.info("Passphrase changed successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to change passphrase: {e}")
            raise EncryptionError("passphrase change", str(e))
    
    def backup_keys(self, backup_path: Path) -> bool:
        """
        Create a backup of the encryption keys.
        
        Args:
            backup_path: Path to backup directory
            
        Returns:
            True if backup successful
        """
        try:
            backup_path = Path(backup_path)
            backup_path.mkdir(parents=True, exist_ok=True)
            
            # Copy key files
            import shutil
            for file_path in [self.key_file, self.salt_file, self.config_file]:
                if file_path.exists():
                    backup_file = backup_path / file_path.name
                    shutil.copy2(file_path, backup_file)
                    backup_file.chmod(0o600)
            
            self.logger.info(f"Keys backed up to {backup_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to backup keys: {e}")
            raise StorageError(str(backup_path), f"backup failed: {e}")
    
    def restore_keys(self, backup_path: Path) -> bool:
        """
        Restore encryption keys from backup.
        
        Args:
            backup_path: Path to backup directory
            
        Returns:
            True if restore successful
        """
        try:
            backup_path = Path(backup_path)
            
            # Verify backup files exist
            backup_files = [backup_path / f.name for f in [self.key_file, self.salt_file, self.config_file]]
            for backup_file in backup_files:
                if not backup_file.exists():
                    raise StorageError(str(backup_file), "backup file not found")
            
            # Copy backup files
            import shutil
            for backup_file, target_file in zip(backup_files, [self.key_file, self.salt_file, self.config_file]):
                shutil.copy2(backup_file, target_file)
                target_file.chmod(0o600)
            
            # Reset internal state
            self._master_key = None
            self._fernet = None
            
            self.logger.info(f"Keys restored from {backup_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to restore keys: {e}")
            raise StorageError(str(backup_path), f"restore failed: {e}")


# Global secure storage instance
_secure_storage: Optional[SecureStorage] = None


def get_secure_storage() -> SecureStorage:
    """
    Get the global secure storage instance.
    
    Returns:
        SecureStorage instance
    """
    global _secure_storage
    if _secure_storage is None:
        _secure_storage = SecureStorage()
    return _secure_storage


def initialize_encryption(passphrase: Optional[str] = None) -> bool:
    """
    Initialize the encryption system.
    
    Args:
        passphrase: Optional passphrase for key derivation
        
    Returns:
        True if initialization successful
    """
    try:
        storage = get_secure_storage()
        return storage.initialize(passphrase)
    except Exception as e:
        logger = get_logger('ashyterm.crypto')
        error_msg = str(e) if str(e) else f"Unknown error of type {type(e).__name__}"
        logger.error(f"Failed to initialize encryption: {error_msg}")
        return False


def encrypt_password(password: str) -> str:
    """
    Encrypt a password for secure storage.
    
    Args:
        password: Plain text password
        
    Returns:
        Encrypted password string
    """
    storage = get_secure_storage()
    if not storage.is_initialized():
        raise EncryptionError("password encryption", "Encryption not initialized")
    return storage.encrypt(password)


def decrypt_password(encrypted_password: str) -> str:
    """
    Decrypt a password from storage.
    
    Args:
        encrypted_password: Encrypted password string
        
    Returns:
        Plain text password
    """
    storage = get_secure_storage()
    if not storage.is_initialized():
        raise EncryptionError("password decryption", "Encryption not initialized")
    return storage.decrypt(encrypted_password)


def is_encryption_available() -> bool:
    """Check if encryption functionality is available."""
    return CRYPTO_AVAILABLE


def is_encryption_initialized() -> bool:
    """Check if encryption is initialized and ready."""
    try:
        storage = get_secure_storage()
        return storage.is_initialized()
    except:
        return False


def secure_compare(a: str, b: str) -> bool:
    """
    Secure string comparison to prevent timing attacks.
    
    Args:
        a: First string
        b: Second string
        
    Returns:
        True if strings are equal
    """
    return secrets.compare_digest(a.encode('utf-8'), b.encode('utf-8'))