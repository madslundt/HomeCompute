"""Private, bounded, descriptor-relative backups of Docker writable directories.

Only daemon-reported data directories are accepted. Special files, hard links,
extended attributes and writable file mounts need operator review. Symlinks are
archived as links and never followed while reading, clearing or restoring data.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tarfile

MAX_BYTES = 5 * 1024 ** 3
MAX_ENTRIES = 100000
FORBIDDEN = {'/', '/home', '/srv', '/var', '/var/lib', '/var/lib/docker', '/var/lib/docker/volumes'}


@contextmanager
def directory(path):
    path = str(path)
    if (not path.startswith(('/srv/', '/home/', '/var/lib/docker/volumes/'))
            or path in FORBIDDEN or '..' in Path(path).parts or '\0' in path):
        raise ValueError('writable mount needs a reviewed data directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in Path(path).parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if os.listxattr(fd):
            raise ValueError('directory attributes require a reviewed backup')
        yield fd
    finally:
        os.close(fd)


def entries(fd, prefix=''):
    if prefix.count('/') > 64 or len(prefix) > 4096:
        raise ValueError('data directory nesting needs a reviewed backup')
    for name in sorted(os.listdir(fd)):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        relative = prefix + name
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                if os.listxattr(child):
                    raise ValueError('directory attributes require a reviewed backup')
                yield relative, info, None, None
                yield from entries(child, relative + '/')
            finally:
                os.close(child)
        elif stat.S_ISLNK(info.st_mode):
            yield relative, info, os.readlink(name, dir_fd=fd), None
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                actual = os.fstat(child)
                if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError('data changed during backup')
                if os.listxattr(child):
                    raise ValueError('extended attributes require a reviewed backup')
                yield relative, actual, None, child
            finally:
                os.close(child)
        else:
            raise ValueError('special files and hard links need a reviewed backup')


def scan(sources):
    total = count = 0
    for source in sources:
        with directory(source) as fd:
            for _, info, _, _ in entries(fd):
                total += info.st_size if stat.S_ISREG(info.st_mode) else 0
                count += 1
                if total > MAX_BYTES or count > MAX_ENTRIES:
                    raise ValueError('backup budget requires operator review')
    return {'bytes': total, 'entries': count}


def create(source, destination):
    """Run after stopping the service; create no archive until root is opened."""
    with directory(source) as fd, open(destination, 'xb') as stream:
        os.chmod(destination, 0o600)
        with tarfile.open(fileobj=stream, mode='w') as archive:
            root_info = os.fstat(fd)
            root = tarfile.TarInfo('.')
            root.type, root.uid, root.gid = tarfile.DIRTYPE, root_info.st_uid, root_info.st_gid
            root.mode, root.mtime = stat.S_IMODE(root_info.st_mode), root_info.st_mtime
            archive.addfile(root)
            total = count = 0
            for name, info, target, child in entries(fd):
                count += 1
                total += info.st_size if child is not None else 0
                if total > MAX_BYTES or count > MAX_ENTRIES:
                    raise ValueError('backup budget changed after stop')
                row = tarfile.TarInfo(name)
                row.uid, row.gid, row.mode, row.mtime = info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_mtime
                if target is not None:
                    row.type, row.linkname = tarfile.SYMTYPE, target
                elif child is None:
                    row.type = tarfile.DIRTYPE
                else:
                    row.size = info.st_size
                if child is None:
                    archive.addfile(row)
                else:
                    with os.fdopen(os.dup(child), 'rb') as data:
                        archive.addfile(row, data)
        stream.flush()
        os.fsync(stream.fileno())


def clear(fd):
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                clear(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)


@contextmanager
def parent(fd, name):
    parts = name.split('/')
    if not parts or any(part in {'', '.', '..'} for part in parts) or name.startswith('/'):
        raise ValueError('invalid private archive member')
    current = os.dup(fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        yield current, parts[-1]
    finally:
        os.close(current)


def restore(source, archive_path):
    with directory(source) as fd, tarfile.open(archive_path, 'r:') as archive:
        records = archive.getmembers()
        if (not records or records[0].name != '.' or not records[0].isdir()
                or len(records) > MAX_ENTRIES + 1 or sum(row.size for row in records) > MAX_BYTES
                or any(not (row.isdir() or row.isfile() or row.issym()) for row in records)):
            raise ValueError('invalid private data archive')
        root, records = records[0], records[1:]
        # Validate all member names before clearing any data.
        for row in records:
            if row.name.startswith('/') or any(part in {'', '.', '..'} for part in row.name.split('/')):
                raise ValueError('invalid private archive member')
        clear(fd)
        directories = []
        for row in records:
            with parent(fd, row.name) as (location, name):
                if row.isdir():
                    os.mkdir(name, 0o700, dir_fd=location)
                    directories.append(row)
                elif row.issym():
                    os.symlink(row.linkname, name, dir_fd=location)
                else:
                    child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=location)
                    with os.fdopen(child, 'wb') as output, archive.extractfile(row) as data:
                        while chunk := data.read(1024 * 1024):
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                if not row.isdir():
                    metadata(location, name, row)
        for row in reversed(directories):
            with parent(fd, row.name) as (location, name):
                metadata(location, name, row)
        os.fchown(fd, root.uid, root.gid)
        os.fchmod(fd, root.mode)
        os.utime(fd, (root.mtime, root.mtime))


def metadata(fd, name, row):
    os.chown(name, row.uid, row.gid, dir_fd=fd, follow_symlinks=False)
    if not row.issym():
        os.chmod(name, row.mode, dir_fd=fd, follow_symlinks=False)
    os.utime(name, (row.mtime, row.mtime), dir_fd=fd, follow_symlinks=False)
