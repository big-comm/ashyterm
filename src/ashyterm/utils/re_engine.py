# Use regex module (PCRE2 backend) for ~50% faster matching if available,
# otherwise fall back to the standard re module.
try:
    import regex as engine
except ImportError:
    import re as engine
