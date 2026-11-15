from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StringConstraints, model_validator

from travelmind.schemas.domain import Contract, ShortText, Text

ContractKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
TravelMode = Literal["driving", "transit", "walking", "cycling"]
TravelIntensity = Literal["relaxed", "balanced", "intensive"]


class DestinationTaskCreate(Contract):
    destination: Annotated[Text, Field(max_length=80)]


class Gcj02Point(Contract):
    crs: Literal["gcj02"] = "gcj02"
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)


class AdministrativeDivisions(Contract):
    province: ShortText | None = None
    city: ShortText | None = None
    district: ShortText | None = None
    adcode: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")] | None = None
    citycode: Annotated[str, StringConstraints(pattern=r"^[0-9]{3,4}$")] | None = None


class Destination(Contract):
    provider: Literal["amap"] = "amap"
    poi_id: ShortText
    name: ShortText
    administrative: AdministrativeDivisions
    coordinates: Gcj02Point
    address: ShortText | None = None


class TravelPreferences(Contract):
    start_date: date | None = None
    end_date: date | None = None
    stay_days: PositiveInt | None = None
    transport_modes: list[TravelMode] | None = Field(default=None, min_length=1, max_length=4)
    styles: list[ShortText] | None = Field(default=None, min_length=1, max_length=8)
    intensity: TravelIntensity | None = None
    budget_cny: NonnegativeInt | None = None
    party_size: PositiveInt | None = None
    companions: list[ShortText] | None = Field(default=None, min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_dates_and_choices(self) -> Self:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot precede start_date")
        for choices in (self.transport_modes, self.styles, self.companions):
            if choices and len(set(choices)) != len(choices):
                raise ValueError("travel preferences must not repeat choices")
        return self


class QuestionnaireOption(Contract):
    option_id: ContractKey
    label: ShortText


class QuestionCondition(Contract):
    question_id: ContractKey
    includes_option_id: ContractKey


class QuestionnaireQuestion(Contract):
    question_id: ContractKey
    text: Annotated[Text, Field(max_length=600)]
    kind: Literal["text", "single_choice", "multi_choice", "date", "integer"]
    required: StrictBool = False
    options: list[QuestionnaireOption] = Field(default_factory=list, max_length=10)
    visible_if: QuestionCondition | None = None

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        if (self.kind in ("single_choice", "multi_choice")) != bool(self.options):
            raise ValueError("only choice questions require options")
        if len({item.option_id for item in self.options}) != len(self.options):
            raise ValueError("option IDs must be unique within a question")
        return self


class QuestionnaireDraft(Contract):
    """Structured model output before the task service assigns a questionnaire ID."""

    questions: list[QuestionnaireQuestion] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_questions(self) -> Self:
        earlier: dict[str, QuestionnaireQuestion] = {}
        for question in self.questions:
            if question.question_id in earlier:
                raise ValueError("question IDs must be unique")
            if condition := question.visible_if:
                parent = earlier.get(condition.question_id)
                if parent is None or condition.includes_option_id not in {
                    option.option_id for option in parent.options
                }:
                    raise ValueError("condition must refer to a choice of an earlier question")
            earlier[question.question_id] = question
        return self


class TextAnswer(Contract):
    question_id: ContractKey
    kind: Literal["text"]
    text: Annotated[Text, Field(max_length=4000)]


class ChoiceAnswer(Contract):
    question_id: ContractKey
    kind: Literal["single_choice", "multi_choice"]
    option_ids: list[ContractKey] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if len(set(self.option_ids)) != len(self.option_ids):
            raise ValueError("selected choices must be unique")
        if self.kind == "single_choice" and len(self.option_ids) != 1:
            raise ValueError("single-choice answer requires exactly one choice")
        return self


class DateAnswer(Contract):
    question_id: ContractKey
    kind: Literal["date"]
    value: date


class IntegerAnswer(Contract):
    question_id: ContractKey
    kind: Literal["integer"]
    value: NonnegativeInt


QuestionnaireAnswer = Annotated[
    TextAnswer | ChoiceAnswer | DateAnswer | IntegerAnswer, Field(discriminator="kind")
]


class QuestionnaireSubmission(Contract):
    answers: list[QuestionnaireAnswer] = Field(max_length=12)

    @model_validator(mode="after")
    def unique_answers(self) -> Self:
        if len({answer.question_id for answer in self.answers}) != len(self.answers):
            raise ValueError("each question may be answered only once")
        return self


class ResearchQuestionnaire(QuestionnaireDraft):
    questionnaire_id: UUID
    stage: Literal["destination_search", "destination_confirmation", "research", "travel"]
    candidates: dict[ContractKey, Destination] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        if self.stage == "destination_confirmation":
            first = self.questions[0]
            if (
                len(self.questions) != 1
                or first.kind != "single_choice"
                or not first.required
                or set(self.candidates) != {item.option_id for item in first.options}
                or len({item.poi_id for item in self.candidates.values()}) != len(self.candidates)
            ):
                raise ValueError(
                    "destination confirmation requires one choice from unique candidates"
                )
        elif self.candidates:
            raise ValueError("candidate POIs belong only to destination confirmation")
        if self.stage == "destination_search":
            first = self.questions[0]
            if len(self.questions) != 1 or first.kind != "text" or not first.required:
                raise ValueError("destination refinement requires one text answer")
        if self.stage in ("research", "travel") and any(q.required for q in self.questions):
            raise ValueError("research and travel questions are optional")
        if self.stage == "research":
            interests = next(
                (question for question in self.questions if question.question_id == "interests"),
                None,
            )
            notes = self.questions[-1]
            if (
                interests is None
                or interests.kind != "multi_choice"
                or interests.visible_if is not None
                or "travel_route" not in {item.option_id for item in interests.options}
                or notes.question_id != "additional_notes"
                or notes.kind != "text"
                or notes.visible_if is not None
            ):
                raise ValueError("research questionnaire requires route choice and final notes")
        return self

    def validate_answers(
        self, submission: QuestionnaireSubmission
    ) -> dict[str, QuestionnaireAnswer]:
        answers = {answer.question_id: answer for answer in submission.answers}
        if answers.keys() - {question.question_id for question in self.questions}:
            raise ValueError("answer refers to an unknown question")
        accepted: dict[str, QuestionnaireAnswer] = {}
        for question in self.questions:
            answer = answers.get(question.question_id)
            if condition := question.visible_if:
                parent = accepted.get(condition.question_id)
                if not isinstance(parent, ChoiceAnswer) or condition.includes_option_id not in (
                    parent.option_ids
                ):
                    if answer is not None:
                        raise ValueError("hidden question cannot be answered")
                    continue
            if answer is None:
                if question.required:
                    raise ValueError("required question needs an answer")
                continue
            if answer.kind != question.kind:
                raise ValueError("answer kind differs from the question")
            if isinstance(answer, ChoiceAnswer) and set(answer.option_ids) - {
                item.option_id for item in question.options
            }:
                raise ValueError("answer includes unknown choices")
            accepted[question.question_id] = answer
        return accepted


class ResearchIntakeRead(Contract):
    task_id: UUID
    destination: Destination | None
    questionnaire: ResearchQuestionnaire | None
    travel_preferences: TravelPreferences | None


class ResearchIntakeContext(Contract):
    """Validated planner input assembled from answer snapshots."""

    destination: Destination
    research_questionnaire: ResearchQuestionnaire
    research_submission: QuestionnaireSubmission
    travel_route_requested: StrictBool
    travel_preferences: TravelPreferences | None = None

    @model_validator(mode="after")
    def validate_travel_request(self) -> Self:
        if self.research_questionnaire.stage != "research":
            raise ValueError("planner context requires the research questionnaire")
        answers = self.research_questionnaire.validate_answers(self.research_submission)
        interests = answers.get("interests")
        requested = isinstance(interests, ChoiceAnswer) and "travel_route" in interests.option_ids
        if self.travel_route_requested != requested:
            raise ValueError("route request must match the submitted research interests")
        if not self.travel_route_requested and self.travel_preferences is not None:
            raise ValueError("travel preferences require a route request")
        return self
