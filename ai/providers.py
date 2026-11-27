"""Provider boundary. No real network-backed provider is implemented in P085."""

from dataclasses import dataclass
from typing import Protocol, Sequence

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class ResourceSnapshot:
    resource_id: int
    resource_version: int
    title: str
    text: str
    citations: tuple[dict, ...] = ()


@dataclass(frozen=True)
class StructuredQuestionDraft:
    question_text: str
    choices: tuple[str, ...]
    correct_choice_index: int
    explanation: str = ''
    citation: dict | None = None


class AIProvider(Protocol):
    model_name: str

    def generate_question_drafts(self, *, resource: ResourceSnapshot, prompt_version: int,
                                 timeout_seconds: int) -> Sequence[StructuredQuestionDraft]: ...


class ProviderUnavailable(RuntimeError):
    pass


class ProviderTimeout(ProviderUnavailable):
    """Real adapters must map their 60-second transport timeout here."""


class DisabledProvider:
    model_name = 'disabled'

    def generate_question_drafts(self, *, resource, prompt_version, timeout_seconds=60):
        raise ProviderUnavailable('AI provider is disabled.')


class FakeProvider:
    """Deterministic test double; never selectable in production."""

    model_name = 'fake-question-draft-v1'

    def generate_question_drafts(self, *, resource, prompt_version, timeout_seconds=60):
        citation = resource.citations[0] if resource.citations else {
            'source_id': resource.resource_id,
            'resource_version': resource.resource_version,
            'resource_sha256': '0' * 64,
            'page_number': 1,
        }
        return [
            StructuredQuestionDraft(
                question_text=(
                    f'[{prompt_version}] {resource.title} bo‘yicha qaysi fikr to‘g‘ri?'
                ),
                choices=('Birinchi variant', 'Ikkinchi variant'),
                correct_choice_index=0,
                explanation='Deterministic FakeProvider drafti.',
                citation=citation,
            )
            for _ in range(5)
        ]


def get_configured_provider() -> AIProvider:
    provider_name = getattr(settings, 'AI_PROVIDER', 'disabled').strip().lower()
    if provider_name == 'disabled':
        return DisabledProvider()
    if provider_name == 'fake':
        if settings.APP_ENV == 'production':
            raise ImproperlyConfigured(
                'AI_PROVIDER=fake is not allowed in production.',
            )
        return FakeProvider()
    raise ImproperlyConfigured(
        'No real AI provider is configured in this release.',
    )
