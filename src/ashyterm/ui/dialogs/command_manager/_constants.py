"""Shared constants for command manager package."""

from typing import List, Tuple

# Extraction command builders: (extension_tuple, uses_dest_flag, command_template_with_output, command_template_without)
# For templates: {input} = input file, {dest} = destination flag, {output} = output path
EXTRACT_COMMANDS: List[Tuple[Tuple[str, ...], bool, str, str]] = [
    ((".zip",), False, "unzip {input} -d {output}", "unzip {input}"),
    ((".tar",), True, "tar -xvf {input} {dest}", "tar -xvf {input}"),
    ((".tar.gz", ".tgz"), True, "tar -xzvf {input} {dest}", "tar -xzvf {input}"),
    ((".tar.bz2", ".tbz2"), True, "tar -xjvf {input} {dest}", "tar -xjvf {input}"),
    ((".tar.xz", ".txz"), True, "tar -xJvf {input} {dest}", "tar -xJvf {input}"),
    (
        (".tar.zst", ".tzst"),
        False,
        "zstd -d {input} -c | tar -xvf - -C {output}",
        "zstd -d {input} -c | tar -xvf -",
    ),
    (
        (".tar.lzma", ".tlz"),
        False,
        "lzma -d -c {input} | tar -xvf - -C {output}",
        "lzma -d -c {input} | tar -xvf -",
    ),
    ((".gz",), False, "gunzip {input}", "gunzip {input}"),
    ((".bz2",), False, "bunzip2 {input}", "bunzip2 {input}"),
    ((".xz",), False, "unxz {input}", "unxz {input}"),
    ((".zst",), False, "zstd -d {input}", "zstd -d {input}"),
    ((".lzma",), False, "lzma -d {input}", "lzma -d {input}"),
]
