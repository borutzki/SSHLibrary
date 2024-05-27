import glob
import os

from robot.utils import is_truthy  # type: ignore

from .exceptions import SCPClientException
from .sftp import SFTPClient

try:
    import scp  # type: ignore
except ImportError:
    raise ImportError(
        "Importing SCP library failed. " "Make sure you have SCP installed."
    )


class SCPClient:
    def __init__(self, ssh_client):
        self._scp_client = scp.SCPClient(ssh_client.get_transport())

    def put_file(self, source, destination, scp_preserve_times, *args):
        sources = self._get_put_file_sources(source)
        self._scp_client.put(
            sources, destination, preserve_times=is_truthy(scp_preserve_times)
        )

    def get_file(self, source, destination, scp_preserve_times, *args):
        self._scp_client.get(
            source, destination, preserve_times=is_truthy(scp_preserve_times)
        )

    def put_directory(self, source, destination, scp_preserve_times, *args):
        self._scp_client.put(
            source, destination, True, preserve_times=is_truthy(scp_preserve_times)
        )

    def get_directory(self, source, destination, scp_preserve_times, *args):
        self._scp_client.get(
            source, destination, True, preserve_times=is_truthy(scp_preserve_times)
        )

    def _get_put_file_sources(self, source):
        source = source.replace("/", os.sep)
        if not os.path.exists(source):
            sources = [f for f in glob.glob(source)]
        else:
            sources = [f for f in [source]]
        if not sources:
            msg = "There are no source files matching '%s'." % source
            raise SCPClientException(msg)
        return sources


class SCPTransferClient(SFTPClient):
    def __init__(self, ssh_client, encoding):
        self._scp_client = scp.SCPClient(ssh_client.get_transport())
        super(SCPTransferClient, self).__init__(ssh_client, encoding)

    def _put_file(
        self,
        source,
        destination,
        mode,
        newline,
        path_separator,
        scp_preserve_times=False,
    ):
        self._create_remote_file(destination, mode)
        self._scp_client.put(
            source, destination, preserve_times=is_truthy(scp_preserve_times)
        )

    def _get_file(self, remote_path, local_path, scp_preserve_times=False):
        self._scp_client.get(
            remote_path, local_path, preserve_times=is_truthy(scp_preserve_times)
        )
