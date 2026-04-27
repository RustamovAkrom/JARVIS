class BaseMode:
    name = "base"

    def enable(self):
        raise NotImplementedError
    
    def disable(self):
        raise NotImplementedError
