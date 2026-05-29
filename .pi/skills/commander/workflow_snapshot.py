# Yaxiio v1.1 - AGPLv3
"""L3 Workflow Snapshot — cross-subtask data relay"""
class WorkflowSnapshot:
    def __init__(self):
        self._data = {}
    def put(self, task_id, subtask_id, output):
        if task_id not in self._data: self._data[task_id] = {}
        self._data[task_id][subtask_id] = output
    def get(self, task_id, subtask_id=None):
        if task_id not in self._data: return {}
        if subtask_id: return self._data[task_id].get(subtask_id, {})
        return self._data[task_id]
    def extract_field(self, task_id, source_subtask, field_path):
        data = self.get(task_id, source_subtask)
        for part in field_path.split("."):
            if isinstance(data, dict): data = data.get(part, {})
            else: return None
        return data
    def cleanup(self, task_id):
        self._data.pop(task_id, None)

class SchemaValidator:
    @staticmethod
    def validate_input(payload, schema):
        if not schema or not schema.get("properties"): return payload
        validated = {}
        for key, prop in schema.get("properties", {}).items():
            if key in payload: validated[key] = payload[key]
            elif key in schema.get("required", []): validated[key] = ""
        return validated
    @staticmethod
    def format_output(result, schema):
        if not schema or not schema.get("properties"): return result
        return {key: result[key] for key in schema.get("properties", {}).keys() if key in result}
