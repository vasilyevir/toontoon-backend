"""Совместный кадр: двое и больше в одной картинке.

Ошибка здесь особенная. Она не падает и не выглядит поломкой: картинка
приходит, лицо на ней знакомое — просто это одно лицо на двоих, повторённое
дважды. Человек ждал себя с близким, а получил двух незнакомцев, и заплатил за
это. Поэтому проверяем не «упомянуты ли имена», а ровно те три запрета, из-за
которых модель обычно и слепляет людей.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import pytest

from app.routers.generate import _joint_references
from app.services import prompt_style


class FakeProfile:
    """Профиль в объёме, который нужен сборке кадра."""

    def __init__(self, name: str, references: list[str], media: list[str] | None = None):
        self.name = name
        self.reference_ids = references
        self.media_ids = media if media is not None else references


# ─── Кто есть кто ────────────────────────────────────────────────────────────


def test_cast_clause_binds_each_name_to_its_photo():
    clause = prompt_style.cast_clause(["Igor", "Anya"])
    # Порядок — единственное, чем мы отличаем одного от другого: связь имени и
    # номера снимка должна быть в тексте буквально, а не подразумеваться.
    assert "the first reference photo is Igor" in clause
    assert "the second reference photo is Anya" in clause
    assert "2 different people" in clause


def test_cast_clause_forbids_the_three_ways_people_get_lost():
    clause = prompt_style.cast_clause(["Igor", "Anya", "Mark"])
    assert "never merge their faces" in clause
    assert "never draw the same person twice" in clause
    assert "never leave anyone out" in clause


def test_cast_clause_survives_more_names_than_ordinals():
    # Порядковых слов у нас четыре; на пятом человеке текст обязан остаться
    # осмысленным, а не оборваться на «the».
    clause = prompt_style.cast_clause(list("ABCDE"))
    assert "reference photo 5 is E" in clause


def test_drawn_cast_is_redrawn_not_photographed():
    drawn = prompt_style.cast_clause(["Igor", "Anya"], drawn=True,
                                     medium="an anime illustration")
    photo = prompt_style.cast_clause(["Igor", "Anya"], drawn=False)
    # Носитель назван первым словом: «перерисуй это аниме-иллюстрацией».
    # Иначе модель к моменту, когда узнаёт про технику, уже правит фотографию.
    assert drawn.startswith("redraw this as an anime illustration, never a photograph")
    assert "photographically themselves" in photo


# ─── Сборка промпта ──────────────────────────────────────────────────────────


def test_assemble_puts_the_cast_first():
    prompt = prompt_style.assemble(
        "on a beach at sunset", style_key="anime", is_text=False,
        editing=True, cast=["Igor", "Anya"],
    )
    # Модель читает слева направо, и «нас двое» должно стоять раньше якоря:
    # иначе сначала заказан аниме-кадр, а люди в нём — уточнение.
    assert prompt.startswith("redraw this as an anime illustration")
    assert "2 different people" in prompt
    assert prompt.index("Igor") < prompt.index("anime illustration style")


def test_single_name_keeps_the_old_identity_clause():
    prompt = prompt_style.assemble(
        "on a beach", style_key="anime", is_text=False, editing=True, cast=["Igor"],
    )
    # Одному человеку имя не нужно — ему нужно сходство, и требование про него
    # написано отдельно и подробнее.
    assert "different people" not in prompt
    assert prompt_style.DRAWN_IDENTITY_CLAUSE[:40] in prompt


def test_cast_is_ignored_when_nothing_is_edited():
    # Текст без снимков: сохранять нечего, людей в кадре придумывает модель.
    prompt = prompt_style.assemble(
        "a beach", style_key="anime", is_text=False, editing=False,
        cast=["Igor", "Anya"],
    )
    assert "different people" not in prompt


def test_poster_with_two_people_still_cuts_them_out_of_their_rooms():
    prompt = prompt_style.assemble(
        "birthday poster", style_key="anime", is_text=False, editing=True,
        poster=True, cast=["Igor", "Anya"],
    )
    assert "different people" in prompt
    assert prompt_style.CUTOUT_CLAUSE in prompt


# ─── Отбор снимков ───────────────────────────────────────────────────────────


def test_joint_references_take_one_photo_per_person():
    media, names = _joint_references([
        FakeProfile("Igor", ["med_a", "med_b", "med_c"]),
        FakeProfile("Anya", ["med_d", "med_e"]),
    ])
    # По одному: нумерация в промпте держится только пока у каждого по кадру.
    assert media == ["med_a", "med_d"]
    assert names == ["Igor", "Anya"]


def test_joint_references_skip_a_profile_without_photos():
    media, names = _joint_references([
        FakeProfile("Igor", [], media=[]),
        FakeProfile("Anya", ["med_d"]),
    ])
    # Пустой профиль не должен сдвигать нумерацию: иначе «второй снимок — Аня»
    # укажет на первый, и в кадр поедет не тот человек.
    assert media == ["med_d"]
    # И один человек — это уже не совместный кадр, имён не нужно.
    assert names == []


def test_joint_references_stay_paired():
    picked = [FakeProfile(f"P{i}", [f"med_{i}"]) for i in range(4)]
    media, names = _joint_references(picked)
    assert len(media) == len(names) == 4


# ─── Слова про людей ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "сделай нас на фоне гор",
    "нас с Аней в аниме",
    "draw us at the beach",
    "мы вдвоем на закате",
    "сфоткай меня",
])
def test_words_about_people_ask_for_faces(text):
    from app.routers.generate import _asks_for_self
    # «Нас» — такая же просьба про лица, как и «меня», просто про двоих. Без
    # неё совместный кадр выходил пейзажем без людей.
    assert _asks_for_self(text)


@pytest.mark.parametrize("text", [
    "наступила осень",
    "нарисуй космос",
    "плакат про распродажу",
])
def test_words_without_people_stay_without_people(text):
    from app.routers.generate import _asks_for_self
    assert not _asks_for_self(text)


# ─── Чей выбор уходит в кадр ─────────────────────────────────────────────────


@pytest.fixture
def catalogue(monkeypatch):
    """Профили, которые «лежат в базе», по идентификатору."""
    from app.routers import generate as router

    known = {f"p{i}": FakeProfile(f"P{i}", [f"med_{i}"]) for i in range(6)}

    async def fake_get(db, profile_id, *, user_id):
        return known.get(profile_id)

    monkeypatch.setattr(router.profiles_repo, "get", fake_get)
    return known


class FakeBody:
    def __init__(self, profile_ids=(), profile_id=None):
        self.profile_ids = list(profile_ids)
        self.profile_id = profile_id


async def test_picked_profiles_keep_the_order_of_choosing(catalogue):
    from app.routers.generate import _picked_profiles

    picked = await _picked_profiles(None, "u1", FakeBody(["p2", "p0"]))
    # Порядок выбора — это порядок людей в кадре; отсортировать его «как в
    # списке» значит поменять местами тех, кого человек уже расставил.
    assert [p.name for p in picked] == ["P2", "P0"]


async def test_old_single_field_still_works(catalogue):
    from app.routers.generate import _picked_profiles

    picked = await _picked_profiles(None, "u1", FakeBody(profile_id="p3"))
    assert [p.name for p in picked] == ["P3"]


async def test_single_field_does_not_duplicate_a_chosen_person(catalogue):
    from app.routers.generate import _picked_profiles

    # Приложение шлёт оба поля сразу — первое ради старых сборок сервера.
    # Человек от этого не должен появиться в кадре дважды.
    picked = await _picked_profiles(None, "u1", FakeBody(["p1", "p2"], profile_id="p1"))
    assert [p.name for p in picked] == ["P1", "P2"]


async def test_a_deleted_profile_is_simply_absent(catalogue):
    from app.routers.generate import _picked_profiles

    picked = await _picked_profiles(None, "u1", FakeBody(["p1", "gone", "p2"]))
    assert [p.name for p in picked] == ["P1", "P2"]


async def test_no_more_than_four_people_at_once(catalogue):
    from app.routers.generate import _picked_profiles, MAX_JOINT_PEOPLE

    picked = await _picked_profiles(None, "u1", FakeBody(["p0", "p1", "p2", "p3", "p4"]))
    assert len(picked) == MAX_JOINT_PEOPLE == 4
