"""Read ONNX input dimensions without loading tensors or a GPU session."""

from pathlib import Path


def _fields(data):
    def varint(i):
        value = shift = 0
        while i < len(data) and shift < 70:
            byte = data[i]
            i += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, i
            shift += 7
        raise ValueError("Invalid ONNX protobuf")

    i = 0
    while i < len(data):
        tag, i = varint(i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = varint(i)
        elif wire == 2:
            size, i = varint(i)
            if i + size > len(data):
                raise ValueError("Truncated ONNX protobuf")
            value = data[i : i + size]
            i += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value = data[i : i + size]
            i += size
        else:
            raise ValueError("Unsupported ONNX protobuf wire")
        yield field, value


def fixed_image_shape(path):
    path = Path(path)
    if path.suffix.lower() != ".onnx":
        return None
    for field, graph in _fields(path.read_bytes()):
        if field != 7:
            continue
        for key, input_value in _fields(graph):
            if key != 11:
                continue
            for key, type_value in _fields(input_value):
                if key != 2:
                    continue
                for key, tensor in _fields(type_value):
                    if key != 1:
                        continue
                    for key, shape in _fields(tensor):
                        if key != 2:
                            continue
                        dims = [
                            next((v for k, v in _fields(dim) if k == 1), 0)
                            for _, dim in _fields(shape)
                        ]
                        if len(dims) == 4:
                            return tuple(dims[-2:]) if all(dims[-2:]) else None
    return None
