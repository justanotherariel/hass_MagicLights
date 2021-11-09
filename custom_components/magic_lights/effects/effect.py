class Effect:
    def __init__(self, entities, effect_config):
        self.name = effect_config.pop("name", None)
        self._config = effect_config
        self._entities = entities

    def update(self, entity_states):
        return entity_states