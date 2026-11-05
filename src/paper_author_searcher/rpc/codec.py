"""Converts between the service's JSON-shaped views and the gRPC messages (contract v1).

The REST and gRPC front ends share one set of views (``api.app.resource``, ``service.agent.agent_view``) and one
request validation (the JSON Schemas); this codec only changes their shape. Field names are the same in both. Enum
values differ in spelling (``"pdf-urls"`` against ``MODE_PDF_URLS``): each proto enum value names its REST value in
the ``rest_value`` option, and the codec reads the mapping from the descriptors, so the .proto file is the one
place that pairs them.
"""

from typing import Any, TypeVar

from google.protobuf import json_format
from google.protobuf.descriptor import Descriptor, EnumDescriptor, FieldDescriptor
from google.protobuf.message import Message

from .v1 import paper_search_pb2

M = TypeVar('M', bound=Message)

# Proto enum value 0 is "unspecified": absent in REST.
UNSPECIFIED = 0


class EnumMapping:
    """REST value <-> proto enum value name, for every enum of a proto file, from its ``rest_value`` options."""

    def __init__(self, enums: list[EnumDescriptor]) -> None:
        self.to_proto: dict[str, dict[str, str]] = {}
        self.to_rest: dict[str, dict[str, str]] = {}
        for enum in enums:
            pairs = {
                value.GetOptions().Extensions[paper_search_pb2.rest_value]: value.name
                for value in enum.values
                if value.number != UNSPECIFIED
            }
            if '' in pairs:
                raise ValueError(f'Every value of {enum.full_name} but the unspecified one needs a rest_value')
            self.to_proto[enum.full_name] = pairs
            self.to_rest[enum.full_name] = {name: rest for rest, name in pairs.items()}

    @classmethod
    def of(cls, file: Any) -> 'EnumMapping':
        return cls(list(file.enum_types_by_name.values()))


class MessageCodec:
    def __init__(self, enums: EnumMapping, ignore_unknown_fields: bool = True) -> None:
        self.enums = enums
        # A REST view may gain a field before the .proto does; by default it is left out, not an error.
        self.ignore_unknown_fields = ignore_unknown_fields

    def to_message(self, data: dict[str, Any], message_type: type[M]) -> M:
        """A REST-shaped dict as a ``message_type`` message (REST enum values become proto enum names)."""
        converted = self._walk(data, message_type.DESCRIPTOR, self.enums.to_proto)
        return json_format.ParseDict(converted, message_type(), ignore_unknown_fields=self.ignore_unknown_fields)

    def to_dict(self, message: Message) -> dict[str, Any]:
        """A message as the REST-shaped dict (unset fields left out, enum names back to REST values)."""
        data = json_format.MessageToDict(message, preserving_proto_field_name=True)
        return self._walk(data, message.DESCRIPTOR, self.enums.to_rest)

    def _walk(self, data: dict[str, Any], descriptor: Descriptor, mapping: dict[str, dict[str, str]]) -> dict[str, Any]:
        converted: dict[str, Any] = {}
        for key, value in data.items():
            field = descriptor.fields_by_name.get(key)
            if field is not None and value is not None:
                value = self._value(field, value, mapping)
            # An enum value the other side does not know reads as unset, which proto3 and REST clients both handle.
            if value is not None:
                converted[key] = value
        return converted

    def _value(self, field: FieldDescriptor, value: Any, mapping: dict[str, dict[str, str]]) -> Any:
        if field.message_type is not None and field.message_type.GetOptions().map_entry:
            return value
        if field.label == FieldDescriptor.LABEL_REPEATED and isinstance(value, list):
            return [self._single(field, item, mapping) for item in value]
        return self._single(field, value, mapping)

    def _single(self, field: FieldDescriptor, value: Any, mapping: dict[str, dict[str, str]]) -> Any:
        if field.enum_type is not None and isinstance(value, str):
            return mapping.get(field.enum_type.full_name, {}).get(value)
        if field.message_type is not None and isinstance(value, dict):
            return self._walk(value, field.message_type, mapping)
        return value


def default_codec() -> MessageCodec:
    return MessageCodec(EnumMapping.of(paper_search_pb2.DESCRIPTOR))
