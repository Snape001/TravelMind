"""Export draft component types for standalone frontend development."""

import json
from typing import Any

from pydantic.json_schema import models_json_schema

from travelmind.schemas.api import ErrorResponse, TaskRead
from travelmind.schemas.phase4_events import ResearchBrowserEvent
from travelmind.schemas.phase4_experts import (
    ExpertProfilesRead,
    ExpertSkill,
    ExpertSkillInvocation,
    ExpertSkillResult,
)
from travelmind.schemas.phase4_intake import (
    DestinationTaskCreate,
    QuestionnaireDraft,
    QuestionnaireSubmission,
    ResearchIntakeContext,
    ResearchIntakeRead,
)
from travelmind.schemas.phase4_sources import ResearchSourceRead, SocialEvidence, TravelRoutesRead


def phase4_components() -> dict[str, Any]:
    models = (
        DestinationTaskCreate,
        QuestionnaireDraft,
        QuestionnaireSubmission,
        ResearchIntakeRead,
        ResearchBrowserEvent,
        ResearchSourceRead,
        TravelRoutesRead,
        ExpertProfilesRead,
        ExpertSkill,
        ExpertSkillInvocation,
        ExpertSkillResult,
        SocialEvidence,
        ResearchIntakeContext,
        TaskRead,
        ErrorResponse,
    )
    _, schema = models_json_schema(
        [(model, "validation") for model in models],
        ref_template="#/components/schemas/{model}",
    )
    return {
        "openapi": "3.1.0",
        "info": {"title": "TravelMind Phase 4 contract components", "version": "1.0.0"},
        "paths": {},
        "components": {"schemas": schema["$defs"]},
    }


if __name__ == "__main__":
    print(json.dumps(phase4_components()))
