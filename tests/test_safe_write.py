from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import literature_monitor.safe_write as safe_write_module
from literature_monitor.safe_write import (
    ContentChangedError,
    CompareReadError,
    atomic_create_text,
    atomic_replace_text,
    create_text_exclusive,
    read_text_exact,
    replace_regular_text_if_unchanged,
    replace_text_if_unchanged,
)


def test_read_text_exact_preserves_utf8_and_newlines(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"
    payload = "α\r\nβ\n終"
    path.write_bytes(payload.encode("utf-8"))

    assert read_text_exact(path) == payload


def test_atomic_create_writes_complete_target_content(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"

    atomic_create_text(path, "new\n完整内容\n")

    assert path.read_bytes() == "new\n完整内容\n".encode("utf-8")
    assert tuple(tmp_path.glob(".state.txt.*.tmp")) == ()


def test_atomic_create_refuses_existing_target_without_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.txt"
    path.write_text("external", encoding="utf-8")

    with pytest.raises(FileExistsError):
        atomic_create_text(path, "generated")

    assert path.read_text(encoding="utf-8") == "external"
    assert tuple(tmp_path.glob(".state.txt.*.tmp")) == ()


def test_atomic_replace_writes_complete_target_content(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"
    path.write_text("old", encoding="utf-8")

    atomic_replace_text(path, "new\n完整内容\n")

    assert path.read_bytes() == "new\n完整内容\n".encode("utf-8")


def test_atomic_replace_preserves_existing_mode(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o640)

    atomic_replace_text(path, "new")

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_exclusive_create_writes_complete_content(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"

    create_text_exclusive(path, "created\n完整内容\n")

    assert path.read_bytes() == "created\n完整内容\n".encode("utf-8")


def test_exclusive_create_refuses_existing_target(tmp_path: Path) -> None:
    path = tmp_path / "existing.txt"
    path.write_text("external", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_text_exclusive(path, "generated")

    assert path.read_text(encoding="utf-8") == "external"


@pytest.mark.parametrize("replace", [replace_text_if_unchanged, replace_regular_text_if_unchanged])
def test_compare_before_replace_succeeds_when_contents_are_unchanged(
    tmp_path: Path,
    replace,
) -> None:
    path = tmp_path / "state.txt"
    path.write_bytes(b"original\r\n")

    replace(
        path,
        "updated\n",
        expected_contents="original\r\n",
    )

    assert path.read_bytes() == b"updated\n"


@pytest.mark.parametrize("replace", [replace_text_if_unchanged, replace_regular_text_if_unchanged])
def test_compare_before_replace_refuses_external_edit(tmp_path: Path, replace) -> None:
    path = tmp_path / "state.txt"
    path.write_text("original", encoding="utf-8")
    expected = read_text_exact(path)
    path.write_text("external edit", encoding="utf-8")

    with pytest.raises(ContentChangedError):
        replace(
            path,
            "generated update",
            expected_contents=expected,
        )

    assert path.read_text(encoding="utf-8") == "external edit"


def test_replace_failure_preserves_original_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.txt"
    path.write_text("original", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(safe_write_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_replace_text(path, "updated")

    assert path.read_text(encoding="utf-8") == "original"
    assert tuple(tmp_path.glob(f".{path.name}.*.tmp")) == ()


@pytest.mark.parametrize("replacement", ["symlink", "directory", "fifo", "missing"])
def test_regular_compare_refuses_unsafe_targets_without_reading_or_replacing(
    tmp_path, monkeypatch, replacement,
):
    path = tmp_path / "state.txt"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"original\r\n")
    if replacement == "symlink":
        path.symlink_to(outside)
    elif replacement == "directory":
        path.mkdir()
        (path / "sentinel").write_bytes(b"preserved")
    elif replacement == "fifo":
        os.mkfifo(path)

    def forbidden_read(*args, **kwargs):
        pytest.fail("Unsafe comparison target must not be read")

    monkeypatch.setattr(safe_write_module.os, "fdopen", forbidden_read)

    with pytest.raises(ContentChangedError):
        replace_regular_text_if_unchanged(path, "updated", expected_contents="original\r\n")

    assert outside.read_bytes() == b"original\r\n"
    if replacement == "symlink":
        assert path.is_symlink()
    elif replacement == "directory":
        assert path.is_dir() and (path / "sentinel").read_bytes() == b"preserved"
    elif replacement == "fifo":
        assert stat.S_ISFIFO(path.lstat().st_mode)
    else:
        assert not path.exists()
    assert not tuple(tmp_path.glob(".state.txt.*.tmp"))


def test_regular_compare_rejects_path_substitution_after_open(tmp_path, monkeypatch):
    path = tmp_path / "state.txt"
    preserved = tmp_path / "preserved.txt"
    path.write_bytes(b"original")
    original_open = os.open
    descriptors = []

    def substitute_after_open(target, flags):
        assert flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK
        descriptor = original_open(target, flags)
        descriptors.append(descriptor)
        path.rename(preserved)
        path.symlink_to(preserved)
        return descriptor

    monkeypatch.setattr(safe_write_module.os, "open", substitute_after_open)

    with pytest.raises(ContentChangedError):
        replace_regular_text_if_unchanged(path, "updated", expected_contents="original")

    assert path.is_symlink() and preserved.read_bytes() == b"original"
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_regular_compare_without_no_follow_support_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "state.txt"
    path.write_bytes(b"original")
    monkeypatch.delattr(safe_write_module.os, "O_NOFOLLOW")

    with pytest.raises(CompareReadError):
        replace_regular_text_if_unchanged(path, "updated", expected_contents="original")

    assert path.read_bytes() == b"original"


def original_location(path):
    parent=path.parent.stat();target=path.stat()
    return dict(expected_directory_identity=(parent.st_dev,parent.st_ino),
                expected_file_identity=(target.st_dev,target.st_ino))


def test_identity_bound_replace_preserves_exact_contents_mode_and_cleans_temp(tmp_path,monkeypatch):
    path=tmp_path/'state.txt';path.write_bytes('original\r\n保留\n'.encode());path.chmod(0o640)
    location=original_location(path);replaces=[];replace=os.replace
    def checked_replace(source,destination,*,src_dir_fd,dst_dir_fd):
        assert src_dir_fd==dst_dir_fd and destination==path.name
        opened=os.fstat(src_dir_fd)
        assert (opened.st_dev,opened.st_ino)==location['expected_directory_identity']
        replaces.append(destination)
        replace(source,destination,src_dir_fd=src_dir_fd,dst_dir_fd=dst_dir_fd)
    monkeypatch.setattr(safe_write_module.os,'replace',checked_replace)
    safe_write_module.replace_regular_text_at_identity(path,'updated\r\n終\n',
        expected_contents='original\r\n保留\n',**location)
    assert replaces==[path.name] and path.read_bytes()=='updated\r\n終\n'.encode()
    assert stat.S_IMODE(path.stat().st_mode)==0o640 and list(tmp_path.iterdir())==[path]


@pytest.mark.parametrize('substitution',['parent_symlink','different_directory','same_bytes_inode',
    'symlink','directory','fifo','missing','changed_contents','changed_binary_contents'])
def test_identity_bound_compare_rejects_original_object_or_content_substitution(tmp_path,monkeypatch,substitution):
    directory=tmp_path/'Papers';directory.mkdir();path=directory/'state.txt';path.write_bytes(b'original\r\n')
    location=original_location(path);preserved=tmp_path/'original-object'
    if substitution in ('parent_symlink','different_directory'):
        directory.rename(preserved)
        if substitution=='parent_symlink':directory.symlink_to(preserved,target_is_directory=True)
        else:directory.mkdir();path.write_bytes(b'original\r\n')
        moved=preserved/path.name
    elif substitution in ('changed_contents','changed_binary_contents'):
        path.write_bytes(b'concurrent\r\n' if substitution=='changed_contents' else b'\xff\x00')
        moved=None
    else:
        path.rename(preserved);moved=preserved
        if substitution=='same_bytes_inode':path.write_bytes(b'original\r\n')
        elif substitution=='symlink':path.symlink_to(preserved)
        elif substitution=='directory':path.mkdir();(path/'sentinel').write_bytes(b'human')
        elif substitution=='fifo':os.mkfifo(path)
    replaces=[]
    def forbidden_replace(*args,**kwargs):
        replaces.append(args);pytest.fail('Location/content conflict must not replace anything')
    monkeypatch.setattr(safe_write_module.os,'replace',forbidden_replace)
    with pytest.raises(ContentChangedError):
        safe_write_module.replace_regular_text_at_identity(path,'updated',expected_contents='original\r\n',**location)
    assert not replaces
    if moved is not None:assert moved.read_bytes()==b'original\r\n'
    if substitution=='parent_symlink':assert directory.is_symlink() and path.read_bytes()==b'original\r\n'
    elif substitution in ('same_bytes_inode','different_directory'):assert path.read_bytes()==b'original\r\n'
    elif substitution=='symlink':assert path.is_symlink()
    elif substitution=='directory':assert (path/'sentinel').read_bytes()==b'human'
    elif substitution=='fifo':assert stat.S_ISFIFO(path.lstat().st_mode)
    elif substitution=='missing':assert not path.exists()
    elif substitution=='changed_contents':assert path.read_bytes()==b'concurrent\r\n'
    else:assert path.read_bytes()==b'\xff\x00'
    assert not list(directory.glob('.*.tmp'))


@pytest.mark.parametrize('boundary',['target_open','temporary_open'])
def test_identity_bound_parent_substitution_after_open_preserves_moved_directory(tmp_path,monkeypatch,boundary):
    directory=tmp_path/'Papers';directory.mkdir();path=directory/'state.txt';path.write_bytes(b'original')
    location=original_location(path);preserved=tmp_path/'preserved';open_file=os.open;descriptors=[]
    def opened(name,flags,*args,**kwargs):
        descriptor=open_file(name,flags,*args,**kwargs);descriptors.append(descriptor)
        if kwargs.get('dir_fd') is not None:
            temporary=bool(flags & os.O_CREAT)
            if (boundary=='target_open' and name==path.name) or (boundary=='temporary_open' and temporary):
                directory.rename(preserved);directory.symlink_to(preserved,target_is_directory=True)
        return descriptor
    monkeypatch.setattr(safe_write_module.os,'open',opened)
    with pytest.raises(ContentChangedError):
        safe_write_module.replace_regular_text_at_identity(path,'updated',expected_contents='original',**location)
    assert directory.is_symlink() and (preserved/path.name).read_bytes()==b'original'
    assert list(preserved.iterdir())==[preserved/path.name]
    for descriptor in descriptors:
        with pytest.raises(OSError):os.fstat(descriptor)


def test_identity_bound_replace_failure_preserves_original_and_cleans_temp(tmp_path,monkeypatch):
    path=tmp_path/'state.txt';path.write_bytes(b'original');location=original_location(path)
    def fail(*args,**kwargs):raise OSError('SENTINEL write error')
    monkeypatch.setattr(safe_write_module.os,'replace',fail)
    with pytest.raises(OSError):
        safe_write_module.replace_regular_text_at_identity(path,'updated',expected_contents='original',**location)
    assert path.read_bytes()==b'original' and list(tmp_path.iterdir())==[path]
