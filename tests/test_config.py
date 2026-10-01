from pathlib import Path
from flow_mcp.config import HERE, load_config


def test_defaults_when_no_file(tmp_path):
    c = load_config(tmp_path / "missing.toml")
    assert c.cdp_port == 9333 and c.flow_url == "https://flow.google.com/"
    assert c.profile_dir == HERE / ".edge-profile"
    assert c.work_root is None and c.denied_roots == ()


def test_file_overrides(tmp_path):
    f = tmp_path / "c.toml"; f.write_text("cdp_port = 9400\ncommit_timeout_s = 30\n")
    c = load_config(f)
    assert c.cdp_port == 9400 and c.commit_timeout_s == 30


def test_path_fence_keys(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('work_root = "C:/work"\ndenied_roots = ["C:/work/mount", "E:/"]\n')
    c = load_config(f)
    assert c.work_root == Path("C:/work")
    assert c.denied_roots == (Path("C:/work/mount"), Path("E:/"))
