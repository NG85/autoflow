import os
from typing import IO, Union

from app.file_storage.base import FileStorage
from app.core.config import settings


class LocalFileStorage(FileStorage):
    def path(self, name: str) -> str:
        return os.path.join(settings.LOCAL_FILE_STORAGE_PATH, name)

    def open(self, name: str, mode: str = "rb") -> IO:
        return open(self.path(name), mode)

    def save(self, name: str, content: Union[str, bytes, IO]) -> None:
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            if isinstance(content, (str, bytes)):
                f.write(content.encode('utf-8') if isinstance(content, str) else content)
            else:
                f.write(content.read())

    def delete(self, name: str) -> None:
        os.remove(self.path(name))

    def exists(self, name: str) -> bool:
        return os.path.exists(self.path(name))

    def size(self, name: str) -> int:
        return os.path.getsize(self.path(name))
