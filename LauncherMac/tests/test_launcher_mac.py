"""Test automatici del LauncherMac (logica pura, niente GUI/Mac).

Esecuzione (da WotscClient/):  python -m pytest LauncherMac/tests/ -v
Ramo Mac su Windows:           $env:FAKE_PLATFORM='darwin' (solo per i test
                               che lo usano esplicitamente).
"""

import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mac_core as core


# --- Selezione asset --------------------------------------------------------

def test_select_base_asset_picks_first_7z():
    assets = [
        {"name": "WOTSCLauncher.zip", "browser_download_url": "u0"},
        {"name": "minipatch.zip", "browser_download_url": "u1"},
        {"name": "WOTSC.client.7z", "browser_download_url": "u2"},
    ]
    hit = core.select_base_asset(assets)
    assert hit["name"] == "WOTSC.client.7z"


def test_select_base_asset_none_without_7z():
    assert core.select_base_asset([{"name": "minipatch.zip"}]) is None
    assert core.select_base_asset([]) is None


def test_select_overlay_asset_picks_mac_zip_only():
    assets = [
        {"name": "WOTSC.client.7z", "browser_download_url": "u0"},
        {"name": "minipatch.zip", "browser_download_url": "u1"},
        {"name": "altro-tool.zip", "browser_download_url": "u2"},
        {"name": "WOTSC.client-mac.zip", "browser_download_url": "u3"},
    ]
    hit = core.select_overlay_asset(assets)
    assert hit["name"] == "WOTSC.client-mac.zip"


def test_select_overlay_asset_case_insensitive():
    assets = [{"name": "WOTSC.Client-MAC.ZIP", "browser_download_url": "u"}]
    assert core.select_overlay_asset(assets)["browser_download_url"] == "u"


def test_select_overlay_asset_ignores_non_mac_zip():
    assets = [{"name": "minipatch.zip"}, {"name": "WOTSCLauncher.zip"}]
    assert core.select_overlay_asset(assets) is None


# --- Regole merge ------------------------------------------------------------

def test_should_copy_ignore_always_skips():
    assert core.should_copy("wotsc.exe", False, exclude=[], ignore=["wotsc.exe"]) is False
    assert core.should_copy("sub/wotsc.exe", True, exclude=[], ignore=["wotsc.exe"]) is False


def test_should_copy_exclude_keeps_existing_user_file():
    assert core.should_copy("settings.json", True, exclude=["settings.json"]) is False
    # ...ma alla prima installazione il default va copiato
    assert core.should_copy("settings.json", False, exclude=["settings.json"]) is True


def test_should_copy_plain_file():
    assert core.should_copy("data/art.mul", True, exclude=["settings.json"]) is True


def test_should_copy_ignore_wildcards():
    assert core.should_copy("cuo.pdb", False, ignore=["*.pdb"]) is False
    assert core.should_copy("sub/FNA.dll", True, ignore=["*.dll"]) is False
    assert core.should_copy("sub/data.mul", True, ignore=["*.dll"]) is True


def test_merge_skips_log_dirs(tmp_path):
    src = tmp_path / "src"
    (src / "JournalLogs").mkdir(parents=True)
    (src / "Logs").mkdir(parents=True)
    (src / "JournalLogs" / "a.txt").write_text("log")
    (src / "Logs" / "crash.txt").write_text("crash")
    (src / "ok.txt").write_text("ok")
    dest = tmp_path / "dst"
    dest.mkdir()
    copied, skipped = core.merge_tree(str(src), str(dest))
    assert (dest / "ok.txt").exists() is True
    assert (dest / "JournalLogs").exists() is False
    assert (dest / "Logs").exists() is False
    assert copied == 1


def test_merge_base_then_overlay_wins(tmp_path):
    base = tmp_path / "base"
    dest = tmp_path / "game"
    over = tmp_path / "over"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "data.txt").write_text("base-data")
    (base / "settings.json").write_text("base-default")
    (base / "wotsc.exe").write_text("win-binary")
    (dest).mkdir()
    (dest / "settings.json").write_text("user-settings")
    (over / "sub").mkdir(parents=True)
    (over / "sub" / "data.txt").write_text("overlay-data")
    (over / "wotsc-mac").write_text("mac-binary")

    exclude = ["settings.json"]
    ignore = ["wotsc.exe"]
    copied, skipped = core.merge_tree(str(base), str(dest), exclude=exclude, ignore=ignore)
    assert (dest / "wotsc.exe").exists() is False          # mac-ignore
    assert (dest / "settings.json").read_text() == "user-settings"  # exclude
    assert (dest / "sub" / "data.txt").read_text() == "base-data"

    copied2, _ = core.merge_tree(str(over), str(dest), exclude=exclude)
    assert (dest / "sub" / "data.txt").read_text() == "overlay-data"  # overlay vince
    assert (dest / "wotsc-mac").read_text() == "mac-binary"
    assert copied >= 1 and copied2 == 2


def test_extract_overlay_zip(tmp_path):
    zp = tmp_path / "mac.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("wotsc-mac", "mac-binary")
        zf.writestr("libs/libfoo.dylib", "lib")
    dest = tmp_path / "game"
    dest.mkdir()
    copied, skipped = core.extract_overlay_zip(str(zp), str(dest), exclude=[])
    assert copied == 2
    assert (dest / "wotsc-mac").read_text() == "mac-binary"


# --- OS-specifico (senza eseguire nulla) -------------------------------------

def test_game_binary_per_platform():
    assert core.game_binary("darwin") == "wotsc-mac"
    assert core.game_binary("windows") == "wotsc.exe"


def test_merge_file_vs_dir_never_merges(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dst"
    (src).mkdir()
    (dest / "wotsc").mkdir(parents=True)  # la cartella dati della base...
    (src / "wotsc").write_text("#!/bin/sh")  # ...contro un file omonimo
    copied, skipped = core.merge_tree(str(src), str(dest))
    assert (dest / "wotsc").is_dir() is True
    assert copied == 0 and skipped == 1


def test_process_check_command_mac_uses_pgrep_on_cuo():
    assert core.process_check_command(plat="darwin") == ["pgrep", "-x", "cuo"]
    cmd = core.process_check_command(plat="windows")
    assert cmd[0] == "tasklist"
    assert core.process_name("darwin") == "cuo"
    assert core.process_name("windows") == "wotsc.exe"


def test_dotnet_search_dirs_mac_has_no_programfiles():
    dirs = core.dotnet_search_dirs("darwin")
    assert any("ProgramFiles" in d for d in dirs) is False
    assert any("dotnet" in d for d in dirs) is True


def test_skip_dirs_mac_adds_macos_entries():
    assert ".DS_Store" in core.skip_dirs("darwin")
    assert ".DS_Store" not in core.skip_dirs("windows")


def test_effective_platform_fake(monkeypatch):
    monkeypatch.setenv("FAKE_PLATFORM", "darwin")
    assert core.effective_platform() == "darwin"
    assert core.is_mac() is True
    assert core.game_binary() == "wotsc-mac"


def test_parse_system_profiler():
    out = """
SPDisplaysDataType:
      Chipset Model: Apple M1 Pro
      Chipset Model: Foo Bar 3000
      Resolution: 1512 x 982
"""
    assert core.parse_system_profiler(out) == ["Apple M1 Pro", "Foo Bar 3000"]
    assert core.parse_system_profiler("") == []


def test_ensure_executable_posix(tmp_path):
    f = tmp_path / "wotsc"
    f.write_text("x")
    assert core.ensure_executable(str(f)) is True
    if os.name != "nt":
        assert os.access(str(f), os.X_OK) is True


def test_build_diagnostics_contains_fields():
    txt = core.build_diagnostics({"binary": "wotsc", "folder": "/tmp/g"})
    assert "wotsc" in txt and "/tmp/g" in txt


def test_ini_lists(tmp_path):
    ini = tmp_path / "mac-ignore.ini"
    ini.write_text("[MacIgnore]\nfiles = wotsc.exe, altro.dll\n")
    assert core.load_mac_ignore_list(str(ini)) == ["wotsc.exe", "altro.dll"]
    assert core.load_mac_ignore_list(str(tmp_path / "manca.ini")) == []
    exc = tmp_path / "exclude.ini"
    exc.write_text("[Exclude]\nfiles = settings.json\n")
    assert core.load_exclude_list(str(exc)) == ["settings.json"]


def test_find_local_test_overlay_auto_detect(tmp_path):
    z = tmp_path / "overlay-test.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("wotsc", "prova")
    assert core.find_local_test_overlay(str(tmp_path / "no.ini"), str(tmp_path)) == str(z)


def test_find_local_test_overlay_missing(tmp_path):
    assert core.find_local_test_overlay(str(tmp_path / "no.ini"), str(tmp_path)) is None


def test_find_local_test_overlay_config_precedence(tmp_path):
    auto = tmp_path / "overlay-test.zip"
    with zipfile.ZipFile(auto, "w") as zf:
        zf.writestr("wotsc", "auto")
    custom = tmp_path / "mia-prova.zip"
    with zipfile.ZipFile(custom, "w") as zf:
        zf.writestr("wotsc", "custom")
    cfg = tmp_path / "config.ini"
    cfg.write_text("[Test]\nlocal_overlay = mia-prova.zip\n")
    assert core.find_local_test_overlay(str(cfg), str(tmp_path)) == str(custom)


def test_overlay_test_zip_end_to_end(tmp_path):
    # Simula "logica odierna + zip di prova alla fine": base con exe Windows
    # e default, overlay di prova con binario Mac -> merge finale corretto.
    base = tmp_path / "base"
    dest = tmp_path / "game"
    base.mkdir()
    (base / "wotsc.exe").write_text("win")
    (base / "settings.json").write_text("default")
    dest.mkdir()
    provaz = tmp_path / "overlay-test.zip"
    with zipfile.ZipFile(provaz, "w") as zf:
        zf.writestr("wotsc-mac", "mac-prova")
        zf.writestr("settings.json", "mac-default")
    ignore = ["wotsc.exe"]
    exclude = ["settings.json"]
    c1, _ = core.merge_tree(str(base), str(dest), exclude=exclude, ignore=ignore)
    assert (dest / "wotsc.exe").exists() is False
    c2, s2 = core.extract_overlay_zip(str(provaz), str(dest), exclude=exclude)
    assert (dest / "wotsc-mac").read_text() == "mac-prova"
    assert (dest / "settings.json").read_text() == "default"  # resta il default base
    assert c1 >= 1 and c2 == 1 and s2 == 1


def test_strip_single_top_dir_wrapper(tmp_path):
    ext = tmp_path / "extracted"
    (ext / "WOTSC client" / "wotsc").mkdir(parents=True)
    (ext / "WOTSC client" / "wotsc" / "map0.mul").write_text("data")
    (ext / "WOTSC client" / "settings.json").write_text("{}")
    stripped = core.strip_single_top_dir(str(ext))
    assert stripped == str(ext / "WOTSC client")
    # merge appiattito: i file finiscono alla root di gioco
    dest = tmp_path / "game"
    dest.mkdir()
    c, _ = core.merge_tree(stripped, str(dest))
    assert (dest / "settings.json").exists() is True
    assert (dest / "wotsc" / "map0.mul").read_text() == "data"
    assert c == 2


def test_strip_single_top_dir_flat_unchanged(tmp_path):
    ext = tmp_path / "extracted"
    ext.mkdir()
    (ext / "a.txt").write_text("a")
    (ext / "b").mkdir()
    assert core.strip_single_top_dir(str(ext)) == str(ext)
    assert core.strip_single_top_dir(str(tmp_path / "manca")) == str(tmp_path / "manca")


def test_strip_single_top_dir_ignores_dotfiles(tmp_path):
    ext = tmp_path / "extracted"
    (ext / "WOTSC client").mkdir(parents=True)
    (ext / ".DS_Store").write_text("x")
    assert core.strip_single_top_dir(str(ext)) == str(ext / "WOTSC client")
