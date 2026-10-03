"""Real local descriptor/copy tests; never read or modify a Chrome download."""

from dataclasses import replace
import os
from pathlib import Path
import stat
from uuid import uuid4

import pytest

from literature_monitor.browser_acquisition import BrowserDownloadEvidence
from literature_monitor.pdf_staging import PdfStagingError, stage_download
import literature_monitor.pdf_staging as staging


@pytest.fixture
def setup(tmp_path):
    tmp_path = tmp_path.resolve()  # macOS /var aliases are not source authority.
    source_dir = tmp_path / 'downloads'; source_dir.mkdir()
    source = source_dir / 'source.pdf'; source.write_bytes(b'%PDF-1.7\noriginal PDF bytes')
    stage_root = tmp_path / 'private-temp'; stage_root.mkdir()
    evidence = BrowserDownloadEvidence(uuid4(), 'tab-12', '10.1000/test', 7, 'direct',
        'task_navigation', 'https://example.org/paper', str(source), 'https://example.org/pdf',
        'https://example.org/pdf', 'https://example.org/paper', 'application/pdf',
        source.stat().st_size, source.stat().st_size, '', 1000, 2000)
    kwargs = dict(project_dir=tmp_path/'project', workspace_dir=tmp_path/'workspace',
                  config_path=tmp_path/'config'/'monitor.yaml', browser_profile_dir=tmp_path/'profile',
                  download_dir=source_dir, temp_root=stage_root)
    return source, evidence, kwargs, stage_root


def test_private_staged_pdf_and_cleanup_leave_source_unchanged(setup):
    source, evidence, kwargs, stage_root = setup
    before = source.read_bytes(), source.stat().st_mode, source.stat().st_ino
    with stage_download(evidence, **kwargs) as artifact:
        assert artifact.validate()
        assert artifact.path.read_bytes() == before[0]
        assert artifact.path.parent.parent == stage_root
        assert stat.S_IMODE(artifact.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(artifact.path.parent.stat().st_mode) == 0o700
        assert 'source.pdf' not in repr(artifact)
    assert not artifact.path.exists()
    assert (source.read_bytes(), source.stat().st_mode, source.stat().st_ino) == before
    assert list(stage_root.iterdir()) == []


@pytest.mark.parametrize('kind', ['final_symlink', 'parent_symlink', 'directory', 'fifo', 'missing', 'unreadable'])
def test_unsafe_source_objects_reject_without_blocking(setup, kind):
    source, evidence, kwargs, stage_root = setup
    if kind == 'final_symlink':
        link = source.with_name('link.pdf'); link.symlink_to(source); evidence = replace(evidence, path=str(link))
    elif kind == 'parent_symlink':
        link = source.parent.with_name('alias'); link.symlink_to(source.parent, target_is_directory=True)
        evidence = replace(evidence, path=str(link/source.name))
    elif kind == 'directory':
        evidence = replace(evidence, path=str(source.parent))
    elif kind == 'fifo':
        source.unlink(); os.mkfifo(source)
    elif kind == 'missing':
        source.unlink()
    elif kind == 'unreadable':
        source.chmod(0)
    with pytest.raises(PdfStagingError) as error:
        stage_download(evidence, **kwargs)
    assert str(source) not in str(error.value)
    assert list(stage_root.iterdir()) == []


@pytest.mark.parametrize('path', ['relative.pdf', 'file:///tmp/a.pdf', '/tmp/a\x00.pdf', '/tmp/../a.pdf', '/tmp//a.pdf'])
def test_malformed_paths_reject(setup, path):
    _, evidence, kwargs, _ = setup
    with pytest.raises(PdfStagingError):
        stage_download(replace(evidence, path=path), **kwargs)


@pytest.mark.parametrize('data,limit', [(b'', 100), (b'%PDF' + b'x'*100, 32), (b'<html>login</html>', 100)])
def test_invalid_or_oversized_bytes_reject_despite_pdf_mime(setup, data, limit):
    source, evidence, kwargs, stage_root = setup
    source.write_bytes(data)
    evidence = replace(evidence, file_size=len(data), mime='application/pdf')
    with pytest.raises(PdfStagingError):
        stage_download(evidence, max_bytes=limit, **kwargs)
    assert source.read_bytes() == data
    assert list(stage_root.iterdir()) == []


def test_source_growth_is_bounded_and_partial_staging_cleaned(setup, monkeypatch):
    source, evidence, kwargs, stage_root = setup
    read = os.read
    mutated = False
    def grow(fd, size):
        nonlocal mutated
        if not mutated and os.fstat(fd).st_ino == source.stat().st_ino:
            mutated = True
            with source.open('ab') as file:
                file.write(b'x'*100)
        return read(fd, size)
    monkeypatch.setattr(staging.os, 'read', grow)
    with pytest.raises(PdfStagingError):
        stage_download(evidence, max_bytes=32, **kwargs)
    assert source.exists() and source.stat().st_size > 32
    assert list(stage_root.iterdir()) == []


@pytest.mark.parametrize('parent_swap', [False, True])
def test_path_substitution_during_read_is_rejected(setup, monkeypatch, parent_swap):
    source, evidence, kwargs, stage_root = setup
    read = os.read
    original_inode = source.stat().st_ino
    substituted = False
    def substitute(fd, size):
        nonlocal substituted
        value = read(fd, size)
        if not substituted and os.fstat(fd).st_ino == original_inode:
            substituted = True
            if parent_swap:
                source.parent.rename(source.parent.with_name('original-downloads'))
                source.parent.mkdir(); source.write_bytes(b'%PDF replacement')
            else:
                source.rename(source.with_name('original.pdf')); source.write_bytes(b'%PDF replacement')
        return value
    monkeypatch.setattr(staging.os, 'read', substitute)
    with pytest.raises(PdfStagingError):
        stage_download(evidence, **kwargs)
    assert source.read_bytes() == b'%PDF replacement'
    assert list(stage_root.iterdir()) == []


@pytest.mark.parametrize('root_key', ['project_dir', 'workspace_dir', 'browser_profile_dir', 'download_dir'])
def test_staging_root_cannot_be_protected(setup, root_key):
    _, evidence, kwargs, stage_root = setup
    kwargs[root_key] = stage_root
    with pytest.raises(PdfStagingError):
        stage_download(evidence, **kwargs)


def test_staging_root_cannot_be_config_directory(setup):
    _, evidence, kwargs, stage_root = setup
    kwargs['config_path'] = stage_root/'monitor.yaml'
    with pytest.raises(PdfStagingError):
        stage_download(evidence, **kwargs)


def test_source_size_metadata_disagreement_is_not_complete_evidence(setup):
    _, evidence, kwargs, _ = setup
    with pytest.raises(PdfStagingError):
        stage_download(replace(evidence, file_size=evidence.file_size+1), **kwargs)


def test_leaf_substitution_between_stat_and_open_is_rejected(setup, monkeypatch):
    source, evidence, kwargs, stage_root = setup
    open_file = os.open
    swapped = False
    def swap(name, flags, *args, **kw):
        nonlocal swapped
        if not swapped and name == source.name and kw.get('dir_fd') is not None:
            swapped = True
            source.rename(source.with_name('original.pdf'))
            source.write_bytes(b'%PDF' + b'x'*(evidence.file_size-4))
        return open_file(name, flags, *args, **kw)
    monkeypatch.setattr(staging.os, 'open', swap)
    with pytest.raises(PdfStagingError):
        stage_download(evidence, **kwargs)
    assert source.exists() and list(stage_root.iterdir()) == []


def test_valid_pdf_signature_does_not_depend_on_browser_mime(setup):
    source, evidence, kwargs, _ = setup
    with stage_download(replace(evidence, mime='text/html'), **kwargs) as artifact:
        assert artifact.validate() and artifact.path.read_bytes() == source.read_bytes()


def test_io_failure_cleans_partial_copy_without_logging_source(setup, monkeypatch, caplog):
    source, evidence, kwargs, stage_root = setup
    contents = source.read_bytes()
    def fail(fd, size):
        raise OSError('private source contents and path')
    monkeypatch.setattr(staging.os, 'read', fail)
    with pytest.raises(PdfStagingError) as error:
        stage_download(evidence, **kwargs)
    assert 'private source contents' not in str(error.value)
    assert caplog.records == [] and list(stage_root.iterdir()) == []
    assert source.read_bytes() == contents


def test_later_staged_byte_replacement_invalidates_artifact(setup):
    source, evidence, kwargs, _ = setup
    artifact = stage_download(evidence, **kwargs)
    artifact.path.write_bytes(b'<html>not PDF</html>')
    assert not artifact.validate()
    artifact.cleanup()
    assert source.exists()


@pytest.mark.parametrize('replacement', ['symlink_same_inode', 'same_bytes', 'hardlink_same_inode'])
def test_directory_swap_after_descriptor_acquisition_rejects(setup, monkeypatch, replacement):
    source, evidence, kwargs, _ = setup
    artifact = stage_download(evidence, **kwargs)
    directory = artifact.path.parent
    moved = directory.with_name(directory.name + '-original')
    original = artifact.path.read_bytes()
    open_file = os.open
    swapped = False

    def swap(name, flags, *args, **kw):
        nonlocal swapped
        if not swapped and name == 'artifact.pdf' and kw.get('dir_fd') is not None:
            # The valid directory descriptor/identity has already been acquired.
            assert os.fstat(kw['dir_fd']).st_ino == directory.stat().st_ino
            swapped = True
            directory.rename(moved)
            if replacement == 'symlink_same_inode':
                directory.symlink_to(moved, target_is_directory=True)
            else:
                directory.mkdir()
                if replacement == 'hardlink_same_inode':
                    os.link(moved/'artifact.pdf', directory/'artifact.pdf')
                else:
                    (directory/'artifact.pdf').write_bytes(original)
        return open_file(name, flags, *args, **kw)

    monkeypatch.setattr(staging.os, 'open', swap)
    assert not artifact.validate()
    assert swapped and (moved/'artifact.pdf').read_bytes() == original
    artifact.cleanup()
    assert (moved/'artifact.pdf').read_bytes() == original
    assert (directory/'artifact.pdf').read_bytes() == original
    assert source.read_bytes() == original


def test_cleanup_directory_swap_after_acquisition_does_not_unlink(setup, monkeypatch):
    source, evidence, kwargs, _ = setup
    artifact = stage_download(evidence, **kwargs)
    directory = artifact.path.parent
    moved = directory.with_name(directory.name + '-original')
    stat_file = os.stat
    unlinks = []
    unlink = os.unlink
    swapped = False

    def swap(name, *args, **kw):
        nonlocal swapped
        if not swapped and name == 'artifact.pdf' and kw.get('dir_fd') is not None:
            swapped = True
            directory.rename(moved)
            directory.symlink_to(moved, target_is_directory=True)
        return stat_file(name, *args, **kw)

    def track_unlink(name, *args, **kw):
        unlinks.append(name)
        return unlink(name, *args, **kw)

    monkeypatch.setattr(staging.os, 'stat', swap)
    monkeypatch.setattr(staging.os, 'unlink', track_unlink)
    artifact.cleanup()
    assert swapped and unlinks == []
    assert directory.is_symlink() and (moved/'artifact.pdf').read_bytes() == source.read_bytes()


def test_cleanup_rejects_substituted_leaf_and_parent_before_open(setup):
    source, evidence, kwargs, _ = setup
    artifact = stage_download(evidence, **kwargs)
    original = artifact.path.with_name('original.pdf')
    artifact.path.rename(original)
    artifact.path.symlink_to(source)
    assert not artifact.validate()
    artifact.cleanup()
    assert artifact.path.is_symlink() and original.exists() and source.exists()
    directory = artifact.path.parent
    moved = directory.with_name(directory.name + '-original')
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    assert not artifact.validate()
    artifact.cleanup()
    assert directory.is_symlink() and (moved/'original.pdf').exists() and source.exists()


def test_cleanup_directory_rebinding_after_unlink_does_not_remove_replacement(setup, monkeypatch):
    _, evidence, kwargs, _ = setup
    artifact = stage_download(evidence, **kwargs)
    directory = artifact.path.parent
    moved = directory.with_name(directory.name + '-original')
    unlink = os.unlink

    def swap(name, *args, **kw):
        unlink(name, *args, **kw)
        if name == 'artifact.pdf' and kw.get('dir_fd') is not None:
            directory.rename(moved)
            directory.mkdir()

    monkeypatch.setattr(staging.os, 'unlink', swap)
    artifact.cleanup()
    assert directory.is_dir() and moved.is_dir()
