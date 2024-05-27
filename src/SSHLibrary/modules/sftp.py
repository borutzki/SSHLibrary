import glob
import ntpath
import os
import posixpath
import re
import stat
from fnmatch import fnmatchcase

from robot.utils import is_bytes  # type: ignore

from .exceptions import SFTPClientException
from .pythonforward import LocalPortForwarding


class SFTPClient:
    """Base class for the SFTP implementation.

    Classes derived from this class (i.e. :py:class:`pythonclient.SFTPClient`
    and :py:class:`javaclient.SFTPClient`) provide the concrete and the language
    specific implementations for getting, putting and listing files and
    directories.
    """

    def __init__(self, ssh_client, encoding):
        self.ssh_client = ssh_client
        self._client = ssh_client.open_sftp()
        self._encoding = encoding
        self._homedir = self._absolute_path(b".")

    def _absolute_path(self, path):
        if not self._is_windows_path(path):
            path = self._client.normalize(path)
        if is_bytes(path):
            path = path.decode(self._encoding)
        return path

    def _is_windows_path(self, path):
        return bool(ntpath.splitdrive(path)[0])

    def is_file(self, path):
        """Checks if the `path` points to a regular file on the remote host.

        If the `path` is a symlink, its destination is checked instead.

        :param str path: The path to check.

        :returns: `True`, if the `path` is points to an existing regular file.
            False otherwise.
        """
        try:
            item = self._stat(path)
        except IOError:
            return False
        return item.is_regular()

    def _stat(self, path):
        path = path.encode(self._encoding)
        attributes = self._client.stat(path)
        return SFTPFileInfo("", attributes.st_mode)

    def is_dir(self, path):
        """Checks if the `path` points to a directory on the remote host.

        If the `path` is a symlink, its destination is checked instead.

        :param str path: The path to check.

        :returns: `True`, if the `path` is points to an existing directory.
            False otherwise.
        """
        try:
            item = self._stat(path)
        except IOError:
            return False
        return item.is_directory()

    def list_dir(self, path, pattern=None, absolute=False):
        """Gets the item names, or optionally the absolute paths, on the given
        `path` on the remote host.

        This includes regular files, directories as well as other file types,
        e.g. device files.

        :param str path: The path on the remote host to list.

        :param str pattern: If given, only the item names that match
            the given pattern are returned. Please do note, that the `pattern`
            is never matched against the full path, even if `absolute` is set
            `True`.

        :param bool absolute: If `True`, the absolute paths of the items are
            returned instead of the item names.

        :returns: A list containing either the item names or the absolute
            paths. In both cases, the List is first filtered by the `pattern`
            if it is given.
        """
        return self._list_filtered(path, self._get_item_names, pattern, absolute)

    def _list_filtered(self, path, filter_method, pattern=None, absolute=False):
        self._verify_remote_dir_exists(path)
        items = filter_method(path)
        if pattern:
            items = self._filter_by_pattern(items, pattern)
        if absolute:
            items = self._include_absolute_path(items, path)
        return items

    def _verify_remote_dir_exists(self, path):
        if not self.is_dir(path):
            raise SFTPClientException("There was no directory matching '%s'." % path)

    def _get_item_names(self, path):
        return [item.name for item in self._list(path)]

    def _list(self, path):
        path = path.encode(self._encoding)
        for item in self._client.listdir_attr(path):
            filename = item.filename
            if is_bytes(filename):
                filename = filename.decode(self._encoding)
            yield SFTPFileInfo(filename, item.st_mode)

    def _filter_by_pattern(self, items, pattern):
        return [name for name in items if fnmatchcase(name, pattern)]

    def _include_absolute_path(self, items, path):
        absolute_path = self._absolute_path(path)
        if absolute_path[1:3] == ":\\":
            absolute_path += "\\"
        else:
            absolute_path += "/"
        return [absolute_path + name for name in items]

    def list_files_in_dir(self, path, pattern=None, absolute=False):
        """Gets the file names, or optionally the absolute paths, of the regular
                files on the given `path` on the remote host.
        .
                :param str path: The path on the remote host to list.

                :param str pattern: If given, only the file names that match
                    the given pattern are returned. Please do note, that the `pattern`
                    is never matched against the full path, even if `absolute` is set
                    `True`.

                :param bool absolute: If `True`, the absolute paths of the regular files
                    are returned instead of the file names.

                :returns: A list containing either the regular file names or the absolute
                    paths. In both cases, the List is first filtered by the `pattern`
                    if it is given.
        """
        return self._list_filtered(path, self._get_file_names, pattern, absolute)

    def _get_file_names(self, path):
        return [
            item.name
            for item in self._list(path)
            if item.is_regular()
            or (item.is_link() and not self._is_dir_symlink(path, item.name))
        ]

    def _is_dir_symlink(self, path, item):
        resolved_link = self._readlink("%s/%s" % (path, item))
        return self.is_dir("%s/%s" % (path, resolved_link))

    def list_dirs_in_dir(self, path, pattern=None, absolute=False):
        """Gets the directory names, or optionally the absolute paths, on the
        given `path` on the remote host.

        :param str path: The path on the remote host to list.

        :param str pattern: If given, only the directory names that match
            the given pattern are returned. Please do note, that the `pattern`
            is never matched against the full path, even if `absolute` is set
            `True`.

        :param bool absolute: If `True`, the absolute paths of the directories
            are returned instead of the directory names.

        :returns: A list containing either the directory names or the absolute
            paths. In both cases, the List is first filtered by the `pattern`
            if it is given.
        """
        return self._list_filtered(path, self._get_directory_names, pattern, absolute)

    def _get_directory_names(self, path):
        return [item.name for item in self._list(path) if item.is_directory()]

    def get_directory(
        self,
        source,
        destination,
        scp_preserve_time,
        path_separator="/",
        recursive=False,
    ):
        destination = self.build_destination(source, destination, path_separator)
        return self._get_directory(
            source, destination, path_separator, recursive, scp_preserve_time
        )

    def _get_directory(
        self,
        source,
        destination,
        path_separator="/",
        recursive=False,
        scp_preserve_times=False,
    ):
        r"""Downloads directory(-ies) from the remote host to the local machine,
        optionally with subdirectories included.

        :param str source: The path to the directory on the remote machine.

        :param str destination: The target path on the local machine.
            The destination defaults to the current local working directory.

        :param bool scp_preserve_times: preserve modification time and access time
        of transferred files and directories.

        :param str path_separator: The path separator used for joining the
            paths on the remote host. On Windows, this must be set as `\`.
            The default is `/`, which is also the default on Linux-like systems.

        :param bool recursive: If `True`, the subdirectories in the `source`
            path are downloaded as well.

        :returns: A list of 2-tuples for all the downloaded files. These tuples
            contain the remote path as the first value and the local target
            path as the second.
        """
        source = self._remove_ending_path_separator(path_separator, source)
        self._verify_remote_dir_exists(source)
        files = []
        items = self.list_dir(source)
        if items:
            for item in items:
                remote = source + path_separator + item
                local = os.path.join(destination, item)
                if self.is_file(remote):
                    files += self.get_file(remote, local, scp_preserve_times)
                elif recursive:
                    files += self.get_directory(
                        remote, local, scp_preserve_times, path_separator, recursive
                    )
        else:
            if not os.path.exists(destination):
                os.makedirs(destination)
            files.append((source, destination))
        return files

    def build_destination(self, source, destination, path_separator):
        """Add parent directory from source to destination path if destination is '.'
        or if destination already exists.
        Otherwise the missing intermediate directories are created.

        :return: A new destination path.
        """
        if os.path.exists(destination) or destination == ".":
            fullpath_destination = os.path.join(
                destination, self.get_parent_folder(source, path_separator)
            )
            if not os.path.exists(fullpath_destination):
                os.makedirs(fullpath_destination)
            return fullpath_destination
        else:
            return destination

    def get_parent_folder(self, source, path_separator):
        if source.endswith(path_separator):
            return (source[: -len(path_separator)]).split(path_separator)[-1]
        else:
            return source.split(path_separator)[-1]

    def _remove_ending_path_separator(self, path_separator, source):
        if source.endswith(path_separator):
            source = source[: -len(path_separator)]
        return source

    def get_file(self, source, destination, scp_preserve_times, path_separator="/"):
        r"""Downloads file(s) from the remote host to the local machine.

        :param str source: Must be the path to an existing file on the remote
            machine or a glob pattern.
            Glob patterns, like '*' and '?', can be used in the source, in
            which case all the matching files are downloaded.

        :param str destination: The target path on the local machine.
            If many files are downloaded, e.g. patterns are used in the
            `source`, then this must be a path to an existing directory.
            The destination defaults to the current local working directory.

        :param bool scp_preserve_times: preserve modification time and access time
        of transferred files and directories.

        :param str path_separator: The path separator used for joining the
            paths on the remote host. On Windows, this must be set as `\`.
            The default is `/`, which is also the default on Linux-like systems.

        :returns: A list of 2-tuples for all the downloaded files. These tuples
            contain the remote path as the first value and the local target
            path as the second.
        """
        remote_files = self._get_get_file_sources(source, path_separator)
        if not remote_files:
            msg = "There were no source files matching '%s'." % source
            raise SFTPClientException(msg)
        local_files = self._get_get_file_destinations(remote_files, destination)
        files = list(zip(remote_files, local_files))
        for src, dst in files:
            self._get_file(src, dst, scp_preserve_times)
        return files

    def _get_get_file_sources(self, source, path_separator):
        if path_separator in source:
            path, pattern = source.rsplit(path_separator, 1)
        else:
            path, pattern = "", source
        if not path:
            path = "."
        if not self.is_file(source):
            return [
                filename
                for filename in self.list_files_in_dir(path, pattern, absolute=True)
            ]
        else:
            return [source]

    def _get_get_file_destinations(self, source_files, destination):
        target_is_dir = destination.endswith(os.sep) or destination == "."
        if not target_is_dir and len(source_files) > 1:
            raise SFTPClientException(
                "Cannot copy multiple source files to one " "destination file."
            )
        destination = os.path.abspath(destination.replace("/", os.sep))
        self._create_missing_local_dirs(destination, target_is_dir)
        if target_is_dir:
            return [
                os.path.join(destination, os.path.basename(name))
                for name in source_files
            ]
        return [destination]

    def _create_missing_local_dirs(self, destination, target_is_dir):
        if not target_is_dir:
            destination = os.path.dirname(destination)
        if not os.path.exists(destination):
            os.makedirs(destination)

    def _get_file(self, remote_path, local_path, scp_preserve_times):
        remote_path = remote_path.encode(self._encoding)
        self._client.get(remote_path, local_path)

    def put_directory(
        self,
        source,
        destination,
        scp_preserve_times,
        mode,
        newline,
        path_separator="/",
        recursive=False,
    ):
        r"""Uploads directory(-ies) from the local machine to the remote host,
        optionally with subdirectories included.

        :param str source: The path to the directory on the local machine.

        :param str destination: The target path on the remote host.
            The destination defaults to the user's home at the remote host.

        :param bool scp_preserve_times: preserve modification time and access time
        of transferred files and directories.

        :param str mode: The uploaded files on the remote host are created with
            these modes. The modes are given as traditional Unix octal
            permissions, such as '0600'.

        :param str newline: If given, the newline characters of the uploaded
            files on the remote host are converted to this.

        :param str path_separator: The path separator used for joining the
            paths on the remote host. On Windows, this must be set as `\`.
            The default is `/`, which is also the default on Linux-like systems.

        :param bool recursive: If `True`, the subdirectories in the `source`
            path are uploaded as well.

        :returns: A list of 2-tuples for all the uploaded files. These tuples
            contain the local path as the first value and the remote target
            path as the second.
        """
        self._verify_local_dir_exists(source)
        destination = self._remove_ending_path_separator(path_separator, destination)
        if self.is_dir(destination):
            destination = destination + path_separator + source.rsplit(os.path.sep)[-1]
        return self._put_directory(
            source,
            destination,
            mode,
            newline,
            path_separator,
            recursive,
            scp_preserve_times,
        )

    def _put_directory(
        self,
        source,
        destination,
        mode,
        newline,
        path_separator,
        recursive,
        scp_preserve_times=False,
    ):
        files = []
        items = os.listdir(source)
        if items:
            for item in items:
                local_path = os.path.join(source, item)
                remote_path = destination + path_separator + item
                if os.path.isfile(local_path):
                    files += self.put_file(
                        local_path,
                        remote_path,
                        scp_preserve_times,
                        mode,
                        newline,
                        path_separator,
                    )
                elif recursive and os.path.isdir(local_path):
                    files += self._put_directory(
                        local_path,
                        remote_path,
                        mode,
                        newline,
                        path_separator,
                        recursive,
                        scp_preserve_times,
                    )
        else:
            self._create_missing_remote_path(destination, mode)
            files.append((source, destination))
        return files

    def _verify_local_dir_exists(self, path):
        if not os.path.isdir(path):
            raise SFTPClientException("There was no source path matching '%s'." % path)

    def put_file(
        self,
        sources,
        destination,
        scp_preserve_times,
        mode,
        newline,
        path_separator="/",
    ):
        r"""Uploads the file(s) from the local machine to the remote host.

        :param str sources: Must be the path to an existing file on the remote
            machine or a glob pattern .
            Glob patterns, like '*' and '?', can be used in the source, in
            which case all the matching files are uploaded.

        :param str destination: The target path on the remote host.
            If multiple files are uploaded, e.g. patterns are used in the
            `source`, then this must be a path to an existing directory.
            The destination defaults to the user's home at the remote host.

        :param bool scp_preserve_times: preserve modification time and access time
        of transferred files and directories.

        :param str mode: The uploaded files on the remote host are created with
            these modes. The modes are given as traditional Unix octal
            permissions, such as '0600'. If 'None' value is provided,
            setting permissions will be skipped.

        :param str newline: If given, the newline characters of the uploaded
            files on the remote host are converted to this.

        :param str path_separator: The path separator used for joining the
            paths on the remote host. On Windows, this must be set as `\`.
            The default is `/`, which is also the default on Linux-like systems.

        :returns: A list of 2-tuples for all the uploaded files. These tuples
            contain the local path as the first value and the remote target
            path as the second.
        """
        if mode:
            mode = int(mode, 8)
        newline = {"CRLF": "\r\n", "LF": "\n"}.get(newline.upper(), None)
        local_files = self._get_put_file_sources(sources)
        remote_files, remote_dir = self._get_put_file_destinations(
            local_files, destination, path_separator
        )
        self._create_missing_remote_path(remote_dir, mode)
        files = list(zip(local_files, remote_files))
        for source, destination in files:
            self._put_file(
                source, destination, mode, newline, path_separator, scp_preserve_times
            )
        return files

    def _get_put_file_sources(self, source):
        source = source.replace("/", os.sep)
        if not os.path.exists(source):
            sources = [f for f in glob.glob(source)]
        else:
            sources = [f for f in [source]]
        if not sources:
            msg = "There are no source files matching '%s'." % source
            raise SFTPClientException(msg)
        return sources

    def _get_put_file_destinations(self, sources, destination, path_separator):
        if destination[1:3] == ":" + path_separator:
            destination = path_separator + destination
        destination = self._format_destination_path(destination)
        if destination == ".":
            destination = self._homedir + "/"
        if len(sources) > 1 and destination[-1] != "/" and not self.is_dir(destination):
            raise ValueError(
                "It is not possible to copy multiple source "
                "files to one destination file."
            )
        dir_path, filename = self._parse_path_elements(destination, path_separator)
        if filename:
            files = [path_separator.join([dir_path, filename])]
        else:
            files = [
                path_separator.join([dir_path, os.path.basename(path)])
                for path in sources
            ]
        return files, dir_path

    def _format_destination_path(self, destination):
        destination = destination.replace("\\", "/")
        destination = ntpath.splitdrive(destination)[-1]
        return destination

    def _parse_path_elements(self, destination, path_separator):
        def _isabs(path):
            if destination.startswith(path_separator):
                return True
            if path_separator == "\\" and path[1:3] == ":\\":
                return True
            return False

        if not _isabs(destination):
            destination = path_separator.join([self._homedir, destination])
        if self.is_dir(destination):
            return destination, ""
        return destination.rsplit(path_separator, 1)

    def _create_missing_remote_path(self, path, mode):
        if str(path):
            path = path.encode(self._encoding)
        if path.startswith(b"/"):
            current_dir = b"/"
        else:
            current_dir = self._absolute_path(b".").encode(self._encoding)
        for dir_name in path.split(b"/"):
            if dir_name:
                current_dir = posixpath.join(current_dir, dir_name)
            try:
                self._client.stat(current_dir)
            except Exception:
                if not isinstance(mode, int):
                    mode = int(mode, 8)
                self._client.mkdir(current_dir, mode)

    def _put_file(
        self,
        source,
        destination,
        mode,
        newline,
        path_separator,
        scp_preserve_times=False,
    ):
        remote_file = self._create_remote_file(destination, mode)
        with open(source, "rb") as local_file:
            position = 0
            while True:
                data = local_file.read(4096)
                if not data:
                    break
                if newline:
                    data = re.sub(
                        rb"(\r\n|\r|\n)", newline.encode(self._encoding), data
                    )
                self._write_to_remote_file(remote_file, data, position)
                position += len(data)
            self._close_remote_file(remote_file)

    def _create_remote_file(self, destination, mode):
        file_exists = self.is_file(destination)
        destination = destination.encode(self._encoding)
        remote_file = self._client.file(destination, "wb")
        remote_file.set_pipelined(True)
        if not file_exists and mode:
            self._client.chmod(destination, mode)
        return remote_file

    def _write_to_remote_file(self, remote_file, data, position):
        remote_file.write(data)

    def _close_remote_file(self, remote_file):
        remote_file.close()

    def create_local_ssh_tunnel(
        self, local_port, remote_host, remote_port, bind_address
    ):
        self._create_local_port_forwarder(
            local_port, remote_host, remote_port, bind_address
        )

    def _create_local_port_forwarder(
        self, local_port, remote_host, remote_port, bind_address
    ):
        transport = self.client.get_transport()
        if not transport:
            raise AssertionError("Connection not open")
        self.tunnel = LocalPortForwarding(
            int(remote_port), remote_host, transport, bind_address
        )
        self.tunnel.forward(int(local_port))

    def _readlink(self, path):
        return self._client.readlink(path)


class SFTPFileInfo(object):
    """Wrapper class for the language specific file information objects.

    Returned by the concrete SFTP client implementations.
    """

    def __init__(self, name, mode):
        self.name = name
        self.mode = mode

    def is_regular(self):
        """Checks if this file is a regular file.

        :returns: `True`, if the file is a regular file. False otherwise.
        """
        return stat.S_ISREG(self.mode)

    def is_directory(self):
        """Checks if this file is a directory.

        :returns: `True`, if the file is a regular file. False otherwise.
        """
        return stat.S_ISDIR(self.mode)

    def is_link(self):
        """Checks if this file is a symbolic link.

        :returns: `True`, if the file is a symlink file. False otherwise.
        """
        return stat.S_ISLNK(self.mode)
