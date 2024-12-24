"""
tomlcli.py

A Python CLI tool for advanced manipulation of TOML files, using tomlkit & rich.

"""

import sys
import json
import click
import tomlkit
from pathlib import Path
from typing import Any, Dict, Union
import csv
import io

from rich.console import Console

console = Console()

# -------------------------------------------------------------------
# UTILITIES
# -------------------------------------------------------------------

def parse_key_path(key_path: str) -> list:
    """Split a dotted key path into segments."""
    return [seg.strip() for seg in key_path.split('.') if seg.strip()]

def get_nested_value(data: Union[dict, tomlkit.items.Table], key_path: str) -> Any:
    """Retrieve a nested value from a dict/TOML table given a dotted key path."""
    segments = parse_key_path(key_path)
    current = data
    for seg in segments:
        if seg not in current:
            raise KeyError(f"Key '{seg}' does not exist in path '{key_path}'.")
        current = current[seg]
    return current

def set_nested_value(data: Union[dict, tomlkit.items.Table], key_path: str, value: Any) -> None:
    """Set a nested value in a dict/TOML table, creating intermediate tables if needed."""
    segments = parse_key_path(key_path)
    current = data
    for seg in segments[:-1]:
        if seg not in current or not isinstance(current[seg], (dict, tomlkit.items.Table)):
            current[seg] = tomlkit.table()
        current = current[seg]
    current[segments[-1]] = value

def remove_nested_key(data: Union[dict, tomlkit.items.Table], key_path: str) -> None:
    """Remove a nested key by dotted path."""
    segments = parse_key_path(key_path)
    current = data
    for seg in segments[:-1]:
        if seg not in current or not isinstance(current[seg], (dict, tomlkit.items.Table)):
            raise KeyError(f"Cannot remove path '{key_path}', missing segment '{seg}'.")
        current = current[seg]
    last_key = segments[-1]
    if last_key not in current:
        raise KeyError(f"Key '{last_key}' does not exist in path '{key_path}'.")
    del current[last_key]

def rename_nested_key(data: Union[dict, tomlkit.items.Table], old_path: str, new_path: str) -> None:
    """Rename a key from old_path to new_path."""
    old_val = get_nested_value(data, old_path)
    remove_nested_key(data, old_path)
    set_nested_value(data, new_path, old_val)

def flatten_dict(d: Any, parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
    """Flatten nested dict/TOML tables into { 'a.b': val } form."""
    items = {}
    if isinstance(d, (dict, tomlkit.items.Table)):
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, (dict, tomlkit.items.Table)):
                items.update(flatten_dict(v, new_key, sep=sep))
            else:
                items[new_key] = v
    else:
        items[parent_key] = d
    return items

def parse_snippet(snippet_str: str) -> Any:
    """
    Parse snippet_str with TomlKit if it looks like a TOML structure:
    - starts with '{' => parse as inline table
    - starts with '[' => parse as array
    Then return the raw item. This preserves booleans, etc.
    Example: {enabled=true,level=2} => a TomlKit inline table
    Example: [x,y,z] => a TomlKit array
    """
    try:
        s = snippet_str.strip()
        if s.startswith("{") or s.startswith("["):
            # We'll treat it as inline table or array
            doc = tomlkit.parse(f"x = {s}")
            return doc["x"]
    except Exception:
        pass
    return None

def parse_value(raw_value: str) -> Any:
    """
    Extended parsing:
      1) If raw_value is "true"/"false" => bool
      2) Try parse_snippet for {inline table} or [array]
      3) Try parse as int/float
      4) Else fallback to raw string
    """
    val = raw_value.strip().lower()
    if val == "true":
        return True
    if val == "false":
        return False

    snippet = parse_snippet(raw_value)
    if snippet is not None:
        # snippet is a TomlKit item (inline table or array)
        return snippet

    # numeric parse
    try:
        return int(raw_value)
    except ValueError:
        pass
    try:
        return float(raw_value)
    except ValueError:
        pass

    # fallback
    return raw_value

def search_in_data(data: Any, pattern: str, path_prefix: str = "") -> list:
    """Search recursively for pattern in keys or stringified values."""
    matches = []
    if isinstance(data, (dict, tomlkit.items.Table)):
        for k, v in data.items():
            full_path = f"{path_prefix}.{k}" if path_prefix else k
            if pattern in k or pattern in str(v):
                matches.append(f"{full_path} = {v}")
            if isinstance(v, (dict, tomlkit.items.Table)):
                matches.extend(search_in_data(v, pattern, full_path))
    else:
        if pattern in str(data):
            matches.append(f"{path_prefix} = {data}")
    return matches

def convert_tomlkit_to_dict(data):
    """Recursively convert tomlkit items into native Python dicts/lists/scalars."""
    if isinstance(data, tomlkit.items.Table):
        d = {}
        for k, v in data.items():
            d[k] = convert_tomlkit_to_dict(v)
        return d
    elif isinstance(data, tomlkit.items.AoT):
        return [convert_tomlkit_to_dict(item) for item in data]
    elif isinstance(data, list):
        return [convert_tomlkit_to_dict(item) for item in data]
    else:
        return data

def to_tomlkit_item(value):
    """
    Recursively convert pythonic values (dict, list, bool, etc.) to TomlKit items,
    preserving booleans, etc.
    """
    if isinstance(value, dict):
        tbl = tomlkit.table()
        for k, v in value.items():
            tbl[k] = to_tomlkit_item(v)
        return tbl
    elif isinstance(value, list):
        arr = tomlkit.array()
        for i in value:
            arr.append(to_tomlkit_item(i))
        return arr
    else:
        # tomlkit.item(value) handles bool, str, int, float
        return tomlkit.item(value)

def deep_merge_tomlkit(target, source):
    """
    Deeply merge `source` into `target`, returning the updated `target`.

    - If both `target` and `source` are TomlKit tables, we iterate keys.
    - If `target[key]` is also a table and `source[key]` is a table, we recurse.
    - Otherwise, we overwrite `target[key] = source[key]`.
    - If `target` or `source` is not a table, we simply return `source` to overwrite.

    This ensures that a boolean or scalar in `source` overwrites
    a boolean or scalar in `target`.
    """
    if isinstance(target, tomlkit.items.Table) and isinstance(source, tomlkit.items.Table):
        for key, val in source.items():
            if key in target:
                # Recurse merge if both are tables
                if isinstance(target[key], tomlkit.items.Table) and isinstance(val, tomlkit.items.Table):
                    deep_merge_tomlkit(target[key], val)
                else:
                    target[key] = val
            else:
                target[key] = val
        return target
    else:
        # Overwrite the entire `target` with `source`
        return source

# -------------------------------------------------------------------
# CLI SETUP
# -------------------------------------------------------------------
@click.group()
def cli():
    """A CLI tool to manipulate TOML files with advanced features."""
    pass

# -------------------------------------------------------------------
# LIST-KEYS
# -------------------------------------------------------------------
@cli.command()
@click.argument("filename", type=click.Path(exists=True))
def list_keys(filename):
    """List top-level keys in the TOML file."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            doc = tomlkit.parse(f.read())
        for k in doc.keys():
            click.echo(k)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

# -------------------------------------------------------------------
# GET
# -------------------------------------------------------------------
@cli.command()
@click.argument("filename", type=click.Path(exists=True))
@click.argument("key_path", type=str)
def get(filename, key_path):
    """
    Get a value from the TOML file by dotted path, e.g. server.ssl.enabled
    Prints booleans as 'true'/'false'.
    """
    try:
        with open(filename, "r", encoding="utf-8") as f:
            doc = tomlkit.parse(f.read())
        value = get_nested_value(doc, key_path)

        # If it's a boolean, we print "true"/"false"
        if isinstance(value, bool):
            click.echo("true" if value else "false")
        else:
            # If it's an inline table or array, we convert to TomlKit string
            if isinstance(value, (tomlkit.items.InlineTable, tomlkit.items.Array)):
                txt = tomlkit.dumps(tomlkit.document({"tmp": value}))
                # "tmp = { ... }" => just get the part after "tmp = "
                # We'll strip newlines. This is a bit naive but works for the test scenario.
                out = txt.strip().replace("tmp = ", "")
                click.echo(out)
            else:
                click.echo(str(value))
    except KeyError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unhandled error: {e}", err=True)
        sys.exit(1)

# -------------------------------------------------------------------
# SET
# -------------------------------------------------------------------
@cli.command()
@click.argument("filename", type=click.Path(exists=True))
@click.argument("key_path", type=str)
@click.argument("raw_value", type=str)
def set(filename, key_path, raw_value):
    """
    Set a value in the TOML file by dotted path.
    The raw_value is parsed with extended type parsing.
    This allows e.g. {enabled=true,level=2}, [x,y,z], true/false, etc.
    """
    parsed_value = parse_value(raw_value)

    try:
        with open(filename, "r", encoding="utf-8") as f:
            doc = tomlkit.parse(f.read())

        # If parse_value returned a TomlKit item for a snippet, just use it
        if isinstance(parsed_value, (tomlkit.items.InlineTable, tomlkit.items.Array)):
            new_val = parsed_value
        else:
            # If it's a python bool, int, float, dict, etc., we wrap in TomlKit items
            new_val = to_tomlkit_item(parsed_value)

        set_nested_value(doc, key_path, new_val)

        with open(filename, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))

        # For booleans, print "true"/"false"
        if isinstance(parsed_value, bool):
            val_str = "true" if parsed_value else "false"
        else:
            val_str = str(parsed_value)

        click.echo(f"Successfully set {key_path} = {val_str}")
    except KeyError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unhandled error: {e}", err=True)
        sys.exit(1)

# -------------------------------------------------------------------
# REMOVE
# -------------------------------------------------------------------
@cli.command()
@click.argument("filename", type=click.Path(exists=True))
@click.argument("key_path", type=str)
def remove(filename, key_path):
    """Remove a key from the TOML file by dotted path."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            doc = tomlkit.parse(f.read())

        remove_nested_key(doc, key_path)

        with open(filename, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))

        click.echo(f"Successfully removed '{key_path}'")
    except KeyError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unhandled error: {e}", err=True)
        sys.exit(1)

# -------------------------------------------------------------------
# RENAME
# -------------------------------------------------------------------
@cli.command()
@click.argument("filename", type=click.Path(exists=True))
@click.argument("old_key_path", type=str)
@click.argument("new_key_path", type=str)
def rename(filename, old_key_path, new_key_path):
    """Rename a key from old_key_path to new_key_path."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            doc = tomlkit.parse(f.read())

        rename_nested_key(doc, old_key_path, new_key_path)

        with open(filename, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))

        click.echo(f"Successfully renamed '{old_key_path}' -> '{new_key_path}'")
    except KeyError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unhandled error: {e}", err=True)
        sys.exit(1)

# -------------------------------------------------------------------
# SEARCH
# -------------------------------------------------------------------
@cli.command()
@click.argument("filename", type=click.Path(exists=True))
@click.argument("pattern", type=str)
def search(filename, pattern):
    """Search for a pattern in the keys/values of the TOML file."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            doc = tomlkit.parse(f.read())
        matches = search_in_data(doc, pattern)
        if not matches:
            click.echo("No matches found.")
        else:
            for m in matches:
                click.echo(m)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

# -------------------------------------------------------------------
# BULK-SET
# -------------------------------------------------------------------
@cli.command()
@click.argument("filename", type=click.Path(exists=True))
@click.argument("json_file_or_string", type=str)
def bulk_set(filename, json_file_or_string):
    """
    Bulk-set values from a JSON file or JSON string into the TOML file.
    Example:
        python tomlcli.py bulk-set config.toml data.json
        python tomlcli.py bulk-set config.toml '{"server":{"ssl":{"enabled":true}}}'
    """
    # 1) Parse the JSON data
    try:
        path_candidate = Path(json_file_or_string)
        if path_candidate.exists() and path_candidate.is_file():
            raw = path_candidate.read_text(encoding="utf-8")
            update_data = json.loads(raw)
        else:
            update_data = json.loads(json_file_or_string)
    except Exception as e:
        click.echo(f"Error parsing JSON data: {e}", err=True)
        sys.exit(1)

    # 2) Convert update_data => TomlKit items
    update_table = to_tomlkit_item(update_data)

    # 3) Merge into doc
    try:
        with open(filename, "r", encoding="utf-8") as f:
            doc = tomlkit.parse(f.read())

        merged = deep_merge_tomlkit(doc, update_table)
        # In case deep_merge_tomlkit returns a brand new table
        # (for example if doc was not a table?), reassign doc
        doc = merged

        with open(filename, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
        click.echo("Bulk-set operation successful.")
    except Exception as e:
        click.echo(f"Unhandled error: {e}", err=True)
        sys.exit(1)

# -------------------------------------------------------------------
# EXPORT
# -------------------------------------------------------------------
@cli.command()
@click.argument("filename", type=click.Path(exists=True))
@click.option("--format", "-f", "fmt", type=click.Choice(["plaintext", "csv", "json", "table"]), default="plaintext",
              help="Export format: plaintext, csv, json, or table")
@click.option("--output", "-o", type=click.Path(writable=True), default=None,
              help="If provided, writes output to this file instead of stdout.")
def export(filename, fmt, output):
    """Export the entire TOML in plaintext, csv, json, or Rich-based table."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            doc = tomlkit.parse(f.read())

        flattened = flatten_dict(doc)

        if fmt == "plaintext":
            lines = []
            for k, v in flattened.items():
                val_str = "true" if (isinstance(v, bool) and v) else "false" if isinstance(v, bool) else str(v)
                lines.append(f"{k}\t{val_str}")
            final_output = "\n".join(lines)

        elif fmt == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["key", "value"])
            for k, v in flattened.items():
                val_str = "true" if (isinstance(v, bool) and v) else "false" if isinstance(v, bool) else str(v)
                writer.writerow([k, val_str])
            final_output = buffer.getvalue()

        elif fmt == "json":
            dict_data = convert_tomlkit_to_dict(doc)
            final_output = json.dumps(dict_data, indent=2)

        elif fmt == "table":
            from rich.console import Console
            from rich.table import Table
            table = Table(title="TOML Content")
            table.add_column("Key", style="cyan")
            table.add_column("Value", style="magenta")

            for k, v in flattened.items():
                if isinstance(v, bool):
                    val_str = "true" if v else "false"
                else:
                    val_str = repr(v)
                table.add_row(k, val_str)

            console2 = Console(file=io.StringIO(), width=120)
            console2.print(table)
            final_output = console2.file.getvalue()

        if output:
            with open(output, "w", encoding="utf-8") as out_f:
                out_f.write(final_output)
        else:
            click.echo(final_output)

    except Exception as e:
        click.echo(f"Unhandled error: {e}", err=True)
        sys.exit(1)

# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
if __name__ == "__main__":
    cli()
