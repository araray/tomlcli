"""
test_tomlcli.py

Test suite for the advanced TOML CLI tool (tomlcli.py).
Using pytest and subprocess to test the CLI end to end.

Requires:
    - pytest
    - tomlkit
    - rich
    - click
    - python -m pip install pytest tomlkit rich click
"""

import json
import pytest
import subprocess

CLI_SCRIPT = "tomlcli.py"

@pytest.fixture
def sample_toml(tmp_path):
    """
    Creates a temporary TOML file with nested structures for testing.
    """
    content = """\
[server]
host = "localhost"
port = 8080

[server.ssl]
enabled = false
certificate = "cert.pem"

[database]
user = "admin"
password = "secret"
retries = 3

[feature_flags]
new_login = true
beta_testers = ["alice", "bob"]

[deep]
[deep.nesting]
[deep.nesting.structure]
key1 = "value1"
"""
    p = tmp_path / "sample.toml"
    p.write_text(content, encoding="utf-8")
    return p

def run_cli_command(args, cwd=None):
    """
    Helper to run CLI commands with subprocess and return (stdout, stderr, exitcode).
    """
    process = subprocess.Popen(
        ["python", CLI_SCRIPT] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        text=True
    )
    stdout, stderr = process.communicate()
    return stdout, stderr, process.returncode

def test_list_keys(sample_toml):
    """
    Test listing top-level keys in the TOML file.
    """
    stdout, stderr, exitcode = run_cli_command(["list-keys", str(sample_toml)])
    assert exitcode == 0, f"CLI error: {stderr}"
    # We expect top-level keys: server, database, feature_flags, deep
    assert "server" in stdout
    assert "database" in stdout
    assert "feature_flags" in stdout
    assert "deep" in stdout

def test_get_value_simple(sample_toml):
    """
    Test getting a simple value from the TOML file (server.host).
    """
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "server.host"])
    assert exitcode == 0, f"CLI error: {stderr}"
    # "localhost"
    assert "localhost" in stdout

def test_get_value_nested(sample_toml):
    """
    Test getting a nested value from the TOML file (server.ssl.enabled).
    """
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "server.ssl.enabled"])
    assert exitcode == 0, f"CLI error: {stderr}"
    # false
    assert "false" in stdout

def test_set_value(sample_toml):
    """
    Test setting a specific value in the TOML file with extended type parsing.
    We'll set "server.ssl.enabled" to "true" (boolean).
    """
    stdout, stderr, exitcode = run_cli_command(["set", str(sample_toml), "server.ssl.enabled", "true"])
    assert exitcode == 0, f"CLI error: {stderr}"
    # Now check if the file was updated
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "server.ssl.enabled"])
    assert "true" in stdout

def test_set_value_list(sample_toml):
    """
    Test setting a value that parses as a list, e.g. [1,2,3].
    """
    stdout, stderr, exitcode = run_cli_command(["set", str(sample_toml), "feature_flags.beta_testers", "[x,y,z]"])
    assert exitcode == 0, f"CLI error: {stderr}"

    # Now get it back
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "feature_flags.beta_testers"])
    assert exitcode == 0, f"CLI error: {stderr}"
    assert "x" in stdout
    assert "y" in stdout
    assert "z" in stdout

def test_set_value_dict(sample_toml):
    """
    Test setting a value that parses as a dict, e.g. {a=1, b=2}.
    """
    stdout, stderr, exitcode = run_cli_command(["set", str(sample_toml), "server.ssl", "{enabled=true,level=2}"])
    assert exitcode == 0, f"CLI error: {stderr}"

    # Now get the sub-keys back
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "server.ssl.enabled"])
    assert "true" in stdout
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "server.ssl.level"])
    assert "2" in stdout

def test_remove_key(sample_toml):
    """
    Test removing a key from the TOML file.
    """
    stdout, stderr, exitcode = run_cli_command(["remove", str(sample_toml), "database.password"])
    assert exitcode == 0, f"CLI error: {stderr}"
    # Attempt to get it, should fail or not exist
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "database.password"])
    assert exitcode != 0, "Should fail because password was removed"

def test_rename_key(sample_toml):
    """
    Test renaming a nested key (server.ssl.enabled -> server.ssl.enable_tls).
    """
    stdout, stderr, exitcode = run_cli_command(["rename", str(sample_toml), "server.ssl.enabled", "server.ssl.enable_tls"])
    assert exitcode == 0, f"CLI error: {stderr}"

    # Now "server.ssl.enabled" should not exist, but "server.ssl.enable_tls" should
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "server.ssl.enabled"])
    assert exitcode != 0, "Should fail because 'enabled' was renamed"

    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "server.ssl.enable_tls"])
    assert exitcode == 0, f"CLI error: {stderr}"

def test_search(sample_toml):
    """
    Test searching for a pattern in keys or values.
    E.g., searching for 'localhost' should find 'server.host'.
    """
    stdout, stderr, exitcode = run_cli_command(["search", str(sample_toml), "localhost"])
    assert exitcode == 0, f"CLI error: {stderr}"
    # Expect output references "server.host" or the value "localhost"
    assert "server.host" in stdout

def test_bulk_set(sample_toml, tmp_path):
    """
    Test bulk-setting from JSON, e.g.:
    {
      "server": {
        "ssl": {
          "enabled": true
        }
      },
      "database": {
        "new_key": 999
      }
    }
    """
    bulk_data = {
        "server": {"ssl": {"enabled": True}},
        "database": {"new_key": 999}
    }
    json_file = tmp_path / "bulk.json"
    json_file.write_text(json.dumps(bulk_data), encoding="utf-8")

    # Now run bulk-set
    stdout, stderr, exitcode = run_cli_command(["bulk-set", str(sample_toml), str(json_file)])
    assert exitcode == 0, f"CLI error: {stderr}"

    # Check the changes
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "server.ssl.enabled"])
    assert "true" in stdout
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "database.new_key"])
    assert "999" in stdout

def test_export_plaintext(sample_toml):
    """
    Test exporting the entire TOML file in plaintext format.
    """
    stdout, stderr, exitcode = run_cli_command(["export", str(sample_toml), "--format", "plaintext"])
    assert exitcode == 0, f"CLI error: {stderr}"
    # Key-Value lines
    assert "server.host\tlocalhost" in stdout

def test_export_rich_table(sample_toml):
    """
    Test exporting the entire TOML file as a Rich table.
    """
    stdout, stderr, exitcode = run_cli_command(["export", str(sample_toml), "--format", "table"])
    assert exitcode == 0, f"CLI error: {stderr}"
    # We expect some Rich table boundary or text, e.g. "┏", "┃", etc.
    assert "┏" in stdout or "┃" in stdout or "┗" in stdout

def test_export_csv(sample_toml):
    """
    Test exporting the entire TOML file as CSV to stdout.
    """
    stdout, stderr, exitcode = run_cli_command(["export", str(sample_toml), "--format", "csv"])
    assert exitcode == 0, f"CLI error: {stderr}"
    # "key,value" header or similar
    assert "key,value" in stdout

def test_export_json_file(sample_toml, tmp_path):
    """
    Test exporting the entire TOML file as JSON to a file.
    """
    out_file = tmp_path / "output.json"
    stdout, stderr, exitcode = run_cli_command([
        "export", str(sample_toml),
        "--format", "json",
        "--output", str(out_file)
    ])
    assert exitcode == 0, f"CLI error: {stderr}"
    assert out_file.exists(), "Output JSON file should be created."

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert "server" in data
    assert "database" in data

def test_invalid_key_path(sample_toml):
    """
    Test behavior when specifying an invalid key path.
    """
    stdout, stderr, exitcode = run_cli_command(["get", str(sample_toml), "this.does.not.exist"])
    assert exitcode != 0, "Should fail because key path does not exist."
    # Check that some error message is present
    assert "Error:" in stderr or "KeyError" in stderr
