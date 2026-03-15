#!/usr/bin/env python3
"""Fix corrupted translations in ashyterm .po files.

These entries had AI instruction prompts or refusal messages
instead of proper translations.
"""
import re
import os
import subprocess

LOCALE_DIR = os.path.dirname(os.path.abspath(__file__))

# Correct translations for all problematic entries per language
# Format: { "msgid": { "lang": "correct msgstr" } }
FIXES = {
    # === UI strings (non "How do I" questions) ===
    "Text Only": {
        "pt-BR": "Somente Texto",
    },
    "Include Global Rules": {
        "pt-BR": "Incluir Regras Globais",
    },
    "Negations of above": {
        "el": "Αρνήσεις των παραπάνω",
        "es": "Negaciones de lo anterior",
    },
    "There is not enough free space at the destination to complete this transfer.": {
        "et": "Sihtkohas pole ülekande lõpetamiseks piisavalt vaba ruumi.",
    },

    # === "How do I..." AI assistant example prompts ===
    "How do I audit npm packages?": {
        "el": "Πώς κάνω έλεγχο πακέτων npm;",
    },
    "How do I backup database?": {
        "et": "Kuidas teha andmebaasi varukoopiaid?",
        "fi": "Miten varmuuskopioin tietokannan?",
    },
    "How do I build a Docker image?": {
        "el": "Πώς δημιουργώ μια εικόνα Docker;",
    },
    "How do I change file permissions?": {
        "et": "Kuidas muuta faili õigusi?",
    },
    "How do I check my IP address?": {
        "fr": "Comment vérifier mon adresse IP ?",
    },
    "How do I compress files?": {
        "cs": "Jak komprimuji soubory?",
        "da": "Hvordan komprimerer jeg filer?",
    },
    "How do I copy line in vim?": {
        "el": "Πώς αντιγράφω γραμμή στο vim;",
    },
    "How do I create SSH tunnel?": {
        "et": "Kuidas luua SSH tunnel?",
    },
    "How do I create database user?": {
        "fi": "Miten luon tietokantakäyttäjän?",
    },
    "How do I create requirements.txt?": {
        "de": "Wie erstelle ich eine requirements.txt?",
    },
    "How do I debug Python?": {
        "de": "Wie debugge ich Python?",
    },
    "How do I delete files safely?": {
        "da": "Hvordan sletter jeg filer sikkert?",
        "el": "Πώς διαγράφω αρχεία με ασφάλεια;",
        "et": "Kuidas faile turvaliselt kustutada?",
    },
    "How do I deploy to k8s?": {
        "el": "Πώς κάνω deploy σε k8s;",
    },
    "How do I describe pod?": {
        "de": "Wie beschreibe ich einen Pod?",
        "el": "Πώς περιγράφω ένα pod;",
        "fr": "Comment décrire un pod ?",
    },
    "How do I edit bashrc?": {
        "el": "Πώς επεξεργάζομαι το bashrc;",
        "fr": "Comment modifier le bashrc ?",
    },
    "How do I extract columns?": {
        "fi": "Miten poimin sarakkeita?",
    },
    "How do I format JSON?": {
        "cs": "Jak formátuji JSON?",
        "et": "Kuidas vormindada JSON-i?",
    },
    "How do I format Python code?": {
        "fi": "Miten muotoilen Python-koodia?",
        "pt-BR": "Como formatar código Python?",
    },
    "How do I handle errors?": {
        "fi": "Miten käsittelen virheitä?",
    },
    "How do I initialize npm project?": {
        "da": "Hvordan initialiserer jeg et npm-projekt?",
    },
    "How do I install npm packages?": {
        "et": "Kuidas paigaldada npm pakette?",
    },
    "How do I install pip packages?": {
        "da": "Hvordan installerer jeg pip-pakker?",
        "et": "Kuidas paigaldada pip pakette?",
    },
    "How do I list npm packages?": {
        "fi": "Miten luettelen npm-paketit?",
    },
    "How do I manage Docker volumes?": {
        "et": "Kuidas hallata Docker köiteid?",
    },
    "How do I monitor network traffic?": {
        "de": "Wie überwache ich den Netzwerkverkehr?",
        "el": "Πώς παρακολουθώ την κίνηση δικτύου;",
    },
    "How do I open file in vim?": {
        "cs": "Jak otevřu soubor ve vim?",
    },
    "How do I profile Python code?": {
        "fi": "Miten profiloin Python-koodia?",
    },
    "How do I replace in vim?": {
        "da": "Hvordan erstatter jeg i vim?",
        "et": "Kuidas asendada vim-is?",
    },
    "How do I restart a service?": {
        "fi": "Miten käynnistän palvelun uudelleen?",
    },
    "How do I run Python tests?": {
        "el": "Πώς τρέχω δοκιμές Python;",
    },
    "How do I run a Docker container?": {
        "et": "Kuidas käivitada Docker konteiner?",
    },
    "How do I save in vim?": {
        "el": "Πώς αποθηκεύω στο vim;",
    },
    "How do I search and replace?": {
        "da": "Hvordan søger og erstatter jeg?",
        "et": "Kuidas otsida ja asendada?",
    },
    "How do I search in vim?": {
        "cs": "Jak vyhledávám ve vim?",
        "da": "Hvordan søger jeg i vim?",
        "et": "Kuidas otsida vim-is?",
    },
    "How do I secure file permissions?": {
        "cs": "Jak zabezpečím oprávnění souborů?",
        "fi": "Miten suojaan tiedostojen oikeudet?",
    },
    "How do I sort text?": {
        "fi": "Miten lajittelen tekstiä?",
    },
    "How do I split in vim?": {
        "el": "Πώς κάνω split στο vim;",
    },
    "How do I undo in vim?": {
        "fi": "Miten kumoan vim-issä?",
    },
    "How do I use GPG?": {
        "fi": "Miten käytän GPG:tä?",
    },
    "How do I use command substitution?": {
        "et": "Kuidas kasutada käsuasendust?",
    },
    "How do I use conditionals?": {
        "da": "Hvordan bruger jeg betingelser?",
    },
    "How do I use find with exec?": {
        "fi": "Miten käytän find-komentoa exec-valitsimella?",
    },
    "How do I use fzf?": {
        "fi": "Miten käytän fzf:ää?",
    },
    "How do I use grep?": {
        "el": "Πώς χρησιμοποιώ το grep;",
        "es": "¿Cómo uso grep?",
        "et": "Kuidas kasutada grep-i?",
        "fi": "Miten käytän grep-komentoa?",
    },
    "How do I use htop?": {
        "fi": "Miten käytän htop-sovellusta?",
    },
    "How do I use npx?": {
        "da": "Hvordan bruger jeg npx?",
        "et": "Kuidas kasutada npx-i?",
    },
    "How do I use sed?": {
        "cs": "Jak používám sed?",
        "pt-BR": "Como usar o sed?",
    },
    "How do I use tee?": {
        "fi": "Miten käytän tee-komentoa?",
    },
    "How do I write a bash script?": {
        "fi": "Miten kirjoitan bash-skriptin?",
    },
}


def fix_po_file(filepath, lang):
    """Fix corrupted translations in a .po file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    fixes_applied = 0

    for msgid, lang_fixes in FIXES.items():
        if lang not in lang_fixes:
            continue

        correct_msgstr = lang_fixes[lang]

        # Build regex to find the corrupted msgid/msgstr block
        # Match msgid "..." (possibly multiline) followed by msgstr "..." (possibly multiline)
        escaped_msgid = re.escape(msgid)
        pattern = (
            r'(msgid\s+"' + escaped_msgid + r'"\s*)'
            r'msgstr\s+"[^"]*"(?:\s*"[^"]*")*'
        )

        def replacement(m):
            return m.group(1) + f'msgstr "{correct_msgstr}"'

        new_content, count = re.subn(pattern, replacement, content)
        if count > 0:
            content = new_content
            fixes_applied += count

    if fixes_applied > 0:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

    return fixes_applied


def compile_mo(po_file, mo_dir):
    """Compile .po to .mo file."""
    lang = os.path.basename(po_file).replace('.po', '')
    mo_path = os.path.join(mo_dir, lang, 'LC_MESSAGES', 'ashyterm.mo')
    os.makedirs(os.path.dirname(mo_path), exist_ok=True)
    result = subprocess.run(
        ['msgfmt', '-o', mo_path, po_file],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  WARNING: msgfmt failed for {lang}: {result.stderr.strip()}")
        return False
    return True


def main():
    mo_dir = os.path.join(os.path.dirname(LOCALE_DIR), 'locale_compiled')

    # Determine affected languages
    affected_langs = set()
    for msgid, lang_fixes in FIXES.items():
        affected_langs.update(lang_fixes.keys())

    total_fixes = 0
    for fname in sorted(os.listdir(LOCALE_DIR)):
        if not fname.endswith('.po'):
            continue
        lang = fname.replace('.po', '')
        if lang not in affected_langs:
            continue

        filepath = os.path.join(LOCALE_DIR, fname)
        fixes = fix_po_file(filepath, lang)
        if fixes > 0:
            print(f"Fixed {fixes} entries in {fname}")
            total_fixes += fixes

    print(f"\nTotal fixes: {total_fixes}")

    # Compile all .mo files
    print("\nCompiling .mo files...")
    compiled = 0
    for fname in sorted(os.listdir(LOCALE_DIR)):
        if not fname.endswith('.po'):
            continue
        filepath = os.path.join(LOCALE_DIR, fname)
        if compile_mo(filepath, LOCALE_DIR):
            compiled += 1

    print(f"Compiled {compiled} .mo files")


if __name__ == '__main__':
    main()
