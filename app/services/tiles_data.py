"""The ARTEKI tile catalog (English).

31 tiles across 4 categories, each with 2–4 quick-reply questions, mirroring the
product flow. Featured tiles are the 6 shown first in the chat. This is static
content served to the frontend via ``GET /api/tiles``.
"""
from __future__ import annotations

from functools import lru_cache

from app.models.tile import Category, Question, Tile, TileCategory


def _q(qid: str, text: str, options: list[str], allow_custom: bool = False) -> Question:
    return Question(id=qid, text=text, options=options, allow_custom=allow_custom)


# ─── 🖼️ Image (6) — cost 1 ──────────────────────────────────────────────────
_IMAGE_TILES: list[Tile] = [
    Tile(
        id="morning", category=TileCategory.IMAGE, emoji="☀️", title="Good morning",
        hint="A picture to set the mood", featured=True, cost=1,
        questions=[
            _q("recipient", "Who is it for?", ["A friend", "Mom", "A colleague", "Myself"]),
            _q("mood", "What mood?", ["Warm", "Tender", "Funny"]),
            _q("subject", "What's in the frame?", ["Flowers", "Tea", "Nature", "Animal"]),
            _q("palette", "Color and atmosphere?", ["Pastel", "Bright", "Cozy"]),
        ],
    ),
    Tile(
        id="good_day", category=TileCategory.IMAGE, emoji="🌤️", title="Have a nice day", cost=1,
        questions=[
            _q("recipient", "Who is it for?", ["A friend", "Mom", "A colleague", "Myself"]),
            _q("mood", "What mood?", ["Cheerful", "Sunny", "Warm"]),
            _q("subject", "What's in the frame?", ["Sun", "Flowers", "City", "Nature"]),
            _q("palette", "Color and atmosphere?", ["Bright", "Pastel", "Soft"]),
        ],
    ),
    Tile(
        id="cute_animal", category=TileCategory.IMAGE, emoji="🐾", title="Cute animal",
        hint="A warm picture with a pet", featured=True, cost=1,
        questions=[
            _q("animal", "Which animal?", ["Cat", "Dog", "Rabbit", "Bear", "Other"], allow_custom=True),
            _q("action", "What is it doing?", ["Sleeping", "Playing", "Being funny"]),
            _q("place", "Where is it?", ["At home", "In the forest", "Outside"]),
            _q("mood", "What mood?", ["Funny", "Tender", "Touching"]),
        ],
    ),
    Tile(
        id="nature", category=TileCategory.IMAGE, emoji="🌿", title="Beautiful nature", cost=1,
        questions=[
            _q("place", "What place?", ["Forest", "Mountains", "Sea", "Field"]),
            _q("season", "Season?", ["Spring", "Summer", "Autumn", "Winter"]),
            _q("time", "Time of day?", ["Morning", "Day", "Sunset", "Night"]),
            _q("mood", "What mood?", ["Calm", "Magical", "Vivid"]),
        ],
    ),
    Tile(
        id="cartoon_character", category=TileCategory.IMAGE, emoji="🧸", title="Cartoon character", cost=1,
        questions=[
            _q("kind", "Human or creature?", ["Human", "Animal", "Fairytale creature"]),
            _q("audience", "For whom?", ["Child", "Adult"]),
            _q("personality", "What character?", ["Cheerful", "Kind", "Playful"]),
            _q("place", "Where is it?", ["At home", "In the forest", "In the city"]),
        ],
    ),
    Tile(
        id="fairytale", category=TileCategory.IMAGE, emoji="🏰", title="Fairytale landscape", cost=1,
        questions=[
            _q("place", "What place?", ["Forest", "Castle", "Village", "Sea"]),
            _q("time", "Time of day?", ["Morning", "Sunset", "Night"]),
            _q("special", "What's special in the frame?", ["Magic", "Lights", "Flowers", "Snow"]),
            _q("mood", "What mood?", ["Magical", "Mysterious", "Warm"]),
        ],
    ),
]

# ─── 💌 Postcard (10) — cost 1 ────────────────────────────────────────────────
_POSTCARD_TILES: list[Tile] = [
    Tile(
        id="birthday", category=TileCategory.POSTCARD, emoji="🎂", title="Birthday",
        hint="A warm greeting for someone close", featured=True, cost=1,
        questions=[
            _q("audience", "Adult or child?", ["Adult", "Child"]),
            _q("name", "Name of the birthday person?", ["No name"], allow_custom=True),
            _q("age", "Age turning?", ["1", "5", "10", "18", "30", "50", "60", "70"]),
            _q("text", "What to write on the card?", [
                "Happy Birthday!", "Wishing you health and happiness", "You're the best!",
                "Live long and happily",
            ]),
        ],
    ),
    Tile(
        id="jubilee", category=TileCategory.POSTCARD, emoji="🥂", title="Jubilee", cost=1,
        questions=[
            _q("audience", "For whom?", ["Woman", "Man"]),
            _q("age", "Age turning?", ["50", "55", "60", "65", "70", "75", "80"]),
            _q("mood", "What mood?", ["Solemn", "Warm", "Joyful"]),
            _q("text", "What to write on the card?", [
                "Happy Jubilee!", "Wisdom and health", "You inspire us", "Many happy years!",
            ]),
        ],
    ),
    Tile(
        id="wedding", category=TileCategory.POSTCARD, emoji="💍", title="Wedding", cost=1,
        questions=[
            _q("names", "Names of the bride and groom?", ["No names"], allow_custom=True),
            _q("style", "What style?", ["Tender", "Solemn", "Fairytale"]),
            _q("subject", "What's in the frame?", ["Flowers", "Rings", "Doves"]),
            _q("text", "What to write on the card?", [
                "Love and harmony!", "Live long and happily", "May your love grow stronger",
                "Happiness and harmony",
            ]),
        ],
    ),
    Tile(
        id="couple_anniversary", category=TileCategory.POSTCARD, emoji="💑", title="Anniversary", cost=1,
        questions=[
            _q("years", "How many years together?", ["1", "3", "5", "10", "25", "50"]),
            _q("mood", "What mood?", ["Romantic", "Warm", "Tender"]),
            _q("text", "What to write on the card?", [
                "Happy anniversary!", "Love grows stronger every day", "You inspire us",
                "May it always be so",
            ]),
        ],
    ),
    Tile(
        id="new_year", category=TileCategory.POSTCARD, emoji="🎄", title="New Year",
        hint="A festive winter card", featured=True, cost=1,
        questions=[
            _q("audience", "For whom?", ["A friend", "Family", "Colleagues", "Everyone"]),
            _q("subject", "What's in the frame?", ["Christmas tree", "Santa", "Snow", "Animal of the year"]),
            _q("mood", "What mood?", ["Magical", "Joyful", "Cozy"]),
            _q("text", "What to write?", [
                "Happy New Year!", "May your wishes come true", "Happiness and health",
                "May this year be the best",
            ]),
        ],
    ),
    Tile(
        id="march_8", category=TileCategory.POSTCARD, emoji="🌷", title="March 8", cost=1,
        questions=[
            _q("audience", "For whom?", ["Mom", "A friend", "A colleague", "All women"]),
            _q("subject", "What's in the frame?", ["Flowers", "Spring", "Nature"]),
            _q("mood", "What mood?", ["Tender", "Warm", "Joyful"]),
            _q("text", "What to write?", [
                "Happy March 8!", "You're the best", "Spring and happiness", "Beauty and love",
            ]),
        ],
    ),
    Tile(
        id="may_9", category=TileCategory.POSTCARD, emoji="🎖️", title="Victory Day", cost=1,
        questions=[
            _q("audience", "For whom?", ["Veteran", "Grandfather", "Family", "Everyone"]),
            _q("subject", "What's in the frame?", ["Flowers", "Dove", "St. George ribbon"]),
            _q("mood", "What mood?", ["Solemn", "Warm", "Patriotic"]),
            _q("text", "What to write?", [
                "Happy Victory Day!", "Thank you for the peaceful sky", "We remember and are proud",
                "Eternal glory to the heroes",
            ]),
        ],
    ),
    Tile(
        id="easter", category=TileCategory.POSTCARD, emoji="🐣", title="Easter", cost=1,
        questions=[
            _q("audience", "For whom?", ["Family", "A friend", "Everyone"]),
            _q("subject", "What's in the frame?", ["Eggs", "Easter cake", "Flowers", "Chicks"]),
            _q("mood", "What mood?", ["Bright", "Joyful", "Warm"]),
            _q("text", "What to write?", [
                "Christ is Risen!", "Happy Easter!", "Peace and joy", "May your home be full of warmth",
            ]),
        ],
    ),
    Tile(
        id="mothers_day", category=TileCategory.POSTCARD, emoji="💐", title="Mother's Day", cost=1,
        questions=[
            _q("sender", "From whom?", ["Daughter", "Son", "Grandchildren"]),
            _q("subject", "What's in the frame?", ["Flowers", "Heart", "Nature"]),
            _q("mood", "What mood?", ["Tender", "Warm", "Touching"]),
            _q("text", "What to write?", [
                "Happy Mother's Day!", "You're the best mom in the world", "I love you endlessly",
                "Thank you for everything",
            ]),
        ],
    ),
    Tile(
        id="just_because", category=TileCategory.POSTCARD, emoji="🎉", title="Just because", cost=1,
        questions=[
            _q("recipient", "For whom?", ["A friend", "Mom", "Loved one"]),
            _q("subject", "What's in the frame?", ["Flowers", "Nature", "Animal", "Surprise"]),
            _q("mood", "What mood?", ["Warm", "Funny", "Tender"]),
            _q("text", "What to write?", [
                "Just wanted to greet you", "Thinking of you", "You're special", "Hugs",
            ]),
        ],
    ),
]

# ─── 📣 Announcement (7) — cost 1 ─────────────────────────────────────────────
_ANNOUNCEMENT_TILES: list[Tile] = [
    Tile(
        id="cafe", category=TileCategory.ANNOUNCEMENT, emoji="☕", title="Cafe / restaurant",
        hint="An announcement for your venue", featured=True, cost=1,
        questions=[
            _q("name", "What's the venue called?", ["No name"], allow_custom=True),
            _q("goal", "What are you advertising?", ["Promo", "New menu", "Delivery", "Opening"]),
            _q("mood", "What mood?", ["Cozy", "Bright", "Appetizing"]),
            _q("text", "What text to place?", [
                "Tasty like home", "We're waiting for you", "Try it — you won't regret it",
                "Best coffee in town",
            ]),
        ],
    ),
    Tile(
        id="beauty_salon", category=TileCategory.ANNOUNCEMENT, emoji="💅", title="Beauty salon", cost=1,
        questions=[
            _q("name", "What's the salon called?", ["No name"], allow_custom=True),
            _q("goal", "What are you advertising?", ["Service", "Promo", "Discount", "Opening"]),
            _q("mood", "What mood?", ["Tender", "Stylish", "Bright"]),
            _q("text", "What text to place?", [
                "Beauty made simple", "Come to us", "This week's promo", "You deserve the best",
            ]),
        ],
    ),
    Tile(
        id="handyman", category=TileCategory.ANNOUNCEMENT, emoji="🔧", title="Home handyman", cost=1,
        questions=[
            _q("trade", "What do you do?", ["Plumbing", "Electrics", "Repairs", "Other"], allow_custom=True),
            _q("goal", "What are you advertising?", ["Service", "Promo", "On-site visit"]),
            _q("mood", "What mood?", ["Reliable", "Business", "Friendly"]),
            _q("text", "What text to place?", [
                "Fast and quality", "Always in touch", "10+ years of experience", "Call — we'll come",
            ]),
        ],
    ),
    Tile(
        id="tutor", category=TileCategory.ANNOUNCEMENT, emoji="📚", title="Tutor", cost=1,
        questions=[
            _q("subject", "Which subject do you teach?", ["Math", "Russian", "English", "Physics", "Other"], allow_custom=True),
            _q("audience", "For whom?", ["Children", "Adults", "Exam prep"]),
            _q("mood", "What mood?", ["Friendly", "Professional", "Inspiring"]),
            _q("text", "What text to place?", [
                "I'll explain it clearly", "Individual approach", "First lesson free", "Results guaranteed",
            ]),
        ],
    ),
    Tile(
        id="bakery", category=TileCategory.ANNOUNCEMENT, emoji="🍰", title="Cakes & baking", cost=1,
        questions=[
            _q("name", "What's your business called?", ["No name"], allow_custom=True),
            _q("goal", "What are you advertising?", ["Custom cakes", "Baking", "Promo"]),
            _q("mood", "What mood?", ["Cozy", "Appetizing", "Festive"]),
            _q("text", "What text to place?", [
                "Tasty and made with love", "Cakes for every taste", "We make dreams real",
                "Order — you won't regret it",
            ]),
        ],
    ),
    Tile(
        id="rental", category=TileCategory.ANNOUNCEMENT, emoji="🏠", title="Property rental", cost=1,
        questions=[
            _q("kind", "What are you renting out?", ["Apartment", "Room", "House", "Cottage"]),
            _q("location", "Where is it located?", ["Moscow", "Saint Petersburg", "Online", "Other city"], allow_custom=True),
            _q("mood", "What mood?", ["Cozy", "Modern", "Homey"]),
            _q("text", "What text to place?", [
                "A cozy home awaits you", "Everything for comfort", "A great option", "Move in and live",
            ]),
        ],
    ),
    Tile(
        id="selling", category=TileCategory.ANNOUNCEMENT, emoji="📦", title="Selling items", cost=1,
        questions=[
            _q("item", "What are you selling?", ["Clothes", "Electronics", "Furniture", "Other"], allow_custom=True),
            _q("condition", "Condition?", ["New", "Used", "Like new"]),
            _q("mood", "What mood?", ["Clean", "Bright", "Simple"]),
            _q("text", "What text to place?", [
                "To a good home", "Bargaining welcome", "Urgent", "Good condition",
            ]),
        ],
    ),
]

# ─── 🎬 Video (8) — cost 2 ────────────────────────────────────────────────────
_VIDEO_TILES: list[Tile] = [
    Tile(
        id="animate_photo", category=TileCategory.VIDEO, emoji="📸", title="Animate photo",
        hint="Bring any photo to life", featured=True, needs_photo=True, cost=2,
        questions=[
            _q("target", "What to animate?", ["Face", "Background", "Everything"]),
            _q("movement", "Movement type?", ["Light breathing", "Wind", "Parallax effect"]),
            _q("intensity", "Intensity?", ["Barely noticeable", "Soft", "Expressive"]),
            _q("mood", "What mood?", ["Tender", "Lively", "Magical"]),
        ],
    ),
    Tile(
        id="animate_pet", category=TileCategory.VIDEO, emoji="🐶", title="Animate pet",
        needs_photo=True, cost=2,
        questions=[
            _q("action", "What does the pet do?", ["Looks at the camera", "Moves", "Blinks"]),
            _q("effect", "Effect?", ["Light breathing", "Playful movement", "Wink"]),
            _q("mood", "What mood?", ["Tender", "Playful", "Funny"]),
        ],
    ),
    Tile(
        id="cartoon_character_video", category=TileCategory.VIDEO, emoji="🧸", title="Cartoon character", cost=2,
        questions=[
            _q("hero", "Who's the hero?", ["Human", "Animal", "Creature"]),
            _q("action", "What does it do?", ["Dancing", "Waving", "Laughing", "Surprised"]),
            _q("style", "Style?", ["Pixar", "Anime", "Fairytale", "Cartoon"]),
            _q("palette", "Color and atmosphere?", ["Bright & cheerful", "Soft & tender", "Magical"]),
        ],
    ),
    Tile(
        id="video_greeting", category=TileCategory.VIDEO, emoji="🎬", title="Video greeting", cost=2,
        questions=[
            _q("occasion", "Occasion?", ["Birthday", "Jubilee", "New Year", "Other"]),
            _q("audience", "For whom?", ["Woman", "Man", "Child"]),
            _q("subject", "What's in the frame?", ["Flowers", "Candles", "Balloons", "Nature"]),
            _q("text", "What to write in the greeting?", [
                "Happy Birthday!", "Wishing you health and happiness", "Happy holiday!", "Many years!",
            ]),
        ],
    ),
    Tile(
        id="living_nature", category=TileCategory.VIDEO, emoji="🌿", title="Living nature", cost=2,
        questions=[
            _q("place", "What place?", ["Forest", "Sea", "Mountains", "Field"]),
            _q("time", "Season and time of day?", ["Summer morning", "Autumn sunset", "Winter night", "Spring day"]),
            _q("special", "What's special?", ["Wind", "Fog", "Snow", "Blossoming"]),
            _q("mood", "What mood?", ["Calm", "Magical", "Vivid"]),
        ],
    ),
    Tile(
        id="cute_animal_video", category=TileCategory.VIDEO, emoji="🐾", title="Cute animal", cost=2,
        questions=[
            _q("animal", "Which animal?", ["Cat", "Dog", "Rabbit", "Other"], allow_custom=True),
            _q("action", "What does it do?", ["Sleeping", "Playing", "Surprised"]),
            _q("place", "Where is it?", ["At home", "In the forest", "Outside"]),
            _q("mood", "What mood?", ["Funny", "Tender", "Touching"]),
        ],
    ),
    Tile(
        id="morning_video", category=TileCategory.VIDEO, emoji="☀️", title="Good morning", cost=2,
        questions=[
            _q("subject", "What's in the frame?", ["Flowers", "Nature", "Tea", "Sunrise"]),
            _q("light", "Color and light?", ["Pastel", "Bright", "Golden", "Soft"]),
            _q("mood", "What mood?", ["Tender", "Cheerful", "Warm"]),
            _q("text", "What to write in the video?", [
                "Good morning!", "Have a wonderful day", "Smile", "Have a nice day!",
            ]),
        ],
    ),
    Tile(
        id="inspiring_video", category=TileCategory.VIDEO, emoji="✨", title="Inspiring video", cost=2,
        questions=[
            _q("subject", "What's in the frame?", ["Mountains", "Forest", "Sunset", "Flowers"]),
            _q("light", "Weather and light?", ["Sunny", "Fog", "Golden hour", "Stars"]),
            _q("mood", "What mood?", ["Solemn", "Quiet", "Joyful"]),
            _q("text", "What to write over the video?", [
                "Live life fully", "Dream big", "Every day is a gift", "Anything is possible",
            ]),
        ],
    ),
]


# Free-form follow-up: a single style question.
FREEFORM_STYLE_QUESTION = Question(
    id="style",
    text="What style?",
    options=["Cartoon", "Watercolor", "Pastel", "Fairytale", "Japanese"],
)


@lru_cache
def get_categories() -> list[Category]:
    return [
        Category(id=TileCategory.IMAGE, emoji="🖼️", label="Image", tiles=_IMAGE_TILES),
        Category(id=TileCategory.POSTCARD, emoji="💌", label="Postcard", tiles=_POSTCARD_TILES),
        Category(id=TileCategory.ANNOUNCEMENT, emoji="📣", label="Announcement", tiles=_ANNOUNCEMENT_TILES),
        Category(id=TileCategory.VIDEO, emoji="🎬", label="Video", tiles=_VIDEO_TILES),
    ]


@lru_cache
def _tile_index() -> dict[str, Tile]:
    index: dict[str, Tile] = {}
    for category in get_categories():
        for tile in category.tiles:
            index[tile.id] = tile
    return index


def get_tile(tile_id: str) -> Tile | None:
    return _tile_index().get(tile_id)


def get_featured() -> list[Tile]:
    return [tile for tile in _tile_index().values() if tile.featured]


def all_tiles() -> list[Tile]:
    return list(_tile_index().values())
