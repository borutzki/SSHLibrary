import glob
from pathlib import Path

from .exceptions import SCPClientException
from .sftp import SFTPClient

try:
    import scp  # type: ignore
except ImportError:
    raise ImportError(
        "Importing SCP library failed. " "Make sure you have SCP installed."
    )

from paramiko import SSHClient


class SCPClient:
    """Wrapper for `scp.SCPClient` used for SCP file transfers in `SSHConnection` class."""

    def __init__(self, ssh_client: SSHClient) -> None:
        self.scp: scp.SCPClient = scp.SCPClient(ssh_client.get_transport())

    def put_file(self, source: Path, destination: str, scp_preserve_times: bool, *args):
        sources = self._get_put_file_sources(source)
        self.scp.put(
            files=sources,
            remote_path=destination,
            preserve_times=scp_preserve_times,
        )

    def get_file(self, source: str, destination: Path, scp_preserve_times: bool, *args):
        self.scp.get(
            remote_path=source,
            local_path=destination,
            preserve_times=scp_preserve_times,
        )

    def put_directory(
        self, source: Path, destination: str, scp_preserve_times: bool, *args
    ):
        self.scp.put(
            files=source,
            remote_path=destination,
            recursive=True,
            preserve_times=scp_preserve_times,
        )

    def get_directory(
        self, source: str, destination: Path, scp_preserve_times: bool, *args
    ):
        self.scp.get(
            remote_path=source,
            local_path=destination,
            recursive=True,
            preserve_times=scp_preserve_times,
        )

    def _get_put_file_sources(self, source: Path) -> list[Path]:
        source = Path(source)
        if not source.exists():
            sources = [Path(f) for f in glob.glob(source)]
        else:
            sources = [f for f in [source]]
        if not sources:
            msg = "There are no source files matching '%s'." % source
            raise SCPClientException(msg)
        return sources


class SCPTransferClient(SFTPClient):
    def __init__(self, ssh_client: SSHClient, encoding: str) -> None:
        self.scp: scp.SCPClient = scp.SCPClient(ssh_client.get_transport())
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
        self.scp.put(
            files=source,
            remote_path=destination,
            preserve_times=scp_preserve_times,
        )

    def _get_file(self, remote_path, local_path, scp_preserve_times=False):
        self.scp.get(
            remote_path=remote_path,
            local_path=local_path,
            preserve_times=scp_preserve_times,
        )
