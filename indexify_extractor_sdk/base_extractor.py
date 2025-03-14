from abc import ABC, abstractmethod
from typing import Dict, List, Type, Optional, Union
import json
from importlib import import_module
from typing import get_type_hints

from pydantic import BaseModel, Json


class EmbeddingSchema(BaseModel):
    dim: int
    distance: str


class Embedding(BaseModel):
    values: List[float]
    distance: str


class ExtractorDescription(BaseModel):
    name: str
    version: str
    description: str
    python_dependencies: List[str]
    system_dependencies: List[str]
    embedding_schemas: dict[str, EmbeddingSchema]
    metadata_schemas: dict[str, str]
    input_params: Optional[str]
    input_mime_types: List[str]


class Feature(BaseModel):
    feature_type: str
    name: str
    value: str

    @classmethod
    def embedding(cls, values: List[float], name: str = "embedding", distance="cosine"):
        embedding = Embedding(values=values, distance=distance)
        return cls(
            feature_type="embedding", name=name, value=embedding.model_dump_json()
        )

    @classmethod
    def metadata(cls, value: Json, name: str = "metadata"):
        return cls(feature_type="metadata", name=name, value=json.dumps(value))


class Content(BaseModel):
    content_type: Optional[str]
    data: bytes
    features: List[Feature] = []
    labels: Dict[str, str] = {}

    @classmethod
    def from_text(
        cls, text: str, features: List[Feature] = [], labels: Dict[str, str] = {}
    ):
        return cls(
            content_type="text/plain",
            data=bytes(text, "utf-8"),
            features=features,
            labels=labels,
        )

    @classmethod
    def from_file(cls, path: str):
        import mimetypes

        m = mimetypes.guess_extension(path)
        with open(path, "rb") as f:
            return cls(content_type=m, data=f.read())


class Extractor(ABC):
    name: str = ""

    version: str = "0.0.0"

    system_dependencies: List[str] = []

    python_dependencies: List[str] = []

    description: str = ""

    input_mime_types = ["text/plain"]

    @abstractmethod
    def extract(
        self, content: Content, params: Type[BaseModel] = None
    ) -> List[Content]:
        """
        Extracts information from the content.
        """
        pass

    @abstractmethod
    def sample_input(self) -> Content:
        pass

    def extract_sample_input(self) -> List[Content]:
        return self.extract(self.sample_input())


class ExtractorWrapper:
    def __init__(self, module_name: str, class_name: str):
        self._module = import_module(module_name)
        self._cls = getattr(self._module, class_name)
        self._param_cls = get_type_hints(self._cls.extract).get("params", None)
        self._instance: Extractor = self._cls()

    def extract(self, content: List[Content], params: Json) -> List[List[Content]]:
        params_dict = json.loads(params)
        param_instance = (
            self._param_cls.model_validate(params_dict) if self._param_cls else None
        )

        # This is because the rust side does batching and on python we don't batch
        out = []
        for c in content:
            extracted_data = self._instance.extract(
                Content(content_type=c.content_type, data=bytes(c.data)), param_instance
            )
            out.append(extracted_data)
        return out

    def describe(self, input_params: Type[BaseModel] = None) -> ExtractorDescription:
        s_input = self._instance.sample_input()
        # Come back to this when we can support schemas based on user defined input params
        if input_params is None:
            input_params = self._param_cls() if self._param_cls else None
        out_c: List[Content] = self._instance.extract(s_input, input_params)
        embedding_schemas = {}
        metadata_schemas = {}
        json_schema = self._param_cls.model_json_schema() if self._param_cls else {}
        json_schema["additionalProperties"] = False
        for content in out_c:
            for feature in content.features:
                if feature.feature_type == "embedding":
                    embedding_value: Embedding = Embedding.parse_raw(feature.value)
                    embedding_schema = EmbeddingSchema(
                        dim=len(embedding_value.values),
                        distance=embedding_value.distance,
                    )
                    embedding_schemas[feature.name] = embedding_schema
                elif feature.feature_type == "metadata":
                    metadata_schemas[feature.name] = json.dumps({})
        return ExtractorDescription(
            name=self._instance.name,
            version=self._instance.version,
            description=self._instance.description,
            python_dependencies=self._instance.python_dependencies,
            system_dependencies=self._instance.system_dependencies,
            embedding_schemas=embedding_schemas,
            metadata_schemas=metadata_schemas,
            input_mime_types=self._instance.input_mime_types,
            input_params=json.dumps(json_schema),
        )
