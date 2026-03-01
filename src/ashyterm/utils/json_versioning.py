# ashyterm/utils/json_versioning.py
"""JSON schema versioning and migration support.

Each JSON config file gets a "_version" field. When the schema evolves,
migration functions upgrade data from version N to N+1 incrementally.
"""

from typing import Any, Callable

from .logger import get_logger

logger = get_logger("ashyterm.utils.json_versioning")

# Type alias for migration function: takes dict, returns migrated dict
MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]


def migrate_data(
    data: dict[str, Any],
    current_version: int,
    migrations: dict[int, MigrationFn],
) -> dict[str, Any]:
    """Apply incremental migrations from data's version up to current_version.

    Args:
        data: The loaded JSON data (dict).
        current_version: The latest schema version number.
        migrations: A dict mapping version N to a function that migrates
                    from version N to N+1.

    Returns:
        The migrated data dict with "_version" set to current_version.
    """
    data_version = data.get("_version", 0)

    if data_version > current_version:
        logger.warning(
            f"Data version {data_version} is newer than code version "
            f"{current_version}. Data may be from a newer release."
        )
        return data

    if data_version == current_version:
        return data

    logger.info(f"Migrating data from version {data_version} to {current_version}")

    for version in range(data_version, current_version):
        migration_fn = migrations.get(version)
        if migration_fn is not None:
            logger.info(f"  Applying migration v{version} → v{version + 1}")
            data = migration_fn(data)
        data["_version"] = version + 1

    return data


def stamp_version(data: dict[str, Any], version: int) -> dict[str, Any]:
    """Ensure data has the _version field set before saving."""
    data["_version"] = version
    return data
