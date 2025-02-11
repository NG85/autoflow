import enum

class GraphType(str, enum.Enum):
    general = "general"
    playbook = "playbook"

    def __str__(self):
        return self.value