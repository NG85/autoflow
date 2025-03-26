import enum

class GraphType(str, enum.Enum):
    general = "general"
    playbook = "playbook"
    crm = "crm"

    def __str__(self):
        return self.value