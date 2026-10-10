"""Схема резюме."""

from pydantic import BaseModel, Field

from documents.schemas.base import BaseDocumentData


class ResumeExperience(BaseModel):
    """Одно место работы."""
    company: str = Field(default="", description="Компания")
    position: str = Field(default="", description="Должность")
    period: str = Field(default="", description="Период работы как в документе")


class ResumeEducation(BaseModel):
    """Одно место обучения."""
    institution: str = Field(description="Учебное заведение")
    degree: str = Field(default="", description="Специальность, степень")
    period: str = Field(default="", description="Период обучения")


class ResumeData(BaseDocumentData):
    """Резюме: данные о кандидате."""

    candidate_name: str = Field(default="", description="ФИО кандидата")
    age: str = Field(default="", description="Возраст")
    city: str = Field(default="", description="Город")
    email: str = Field(default="", description="Email")
    phone: str = Field(default="", description="Телефон")
    position: str = Field(default="", description="Желаемая должность")
    skills: list[str] = Field(
        default_factory=list,
        description="Навыки. КАЖДЫЙ навык — отдельный элемент списка. "
                    "Например: ['Python', 'C', 'JavaScript', 'Git'].",
    )
    experience: list[ResumeExperience] = Field(
        default_factory=list,
        description="Опыт работы",
    )
    education: list[ResumeEducation] = Field(
        default_factory=list,
        description="Образование",
    )
    languages: list[str] = Field(
        default_factory=list,
        description="Языки. КАЖДЫЙ язык — отдельный элемент списка.",
    )