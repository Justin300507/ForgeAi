"""
Experience Composer (deterministic).

Thinks in experiences, not pages: what the first 30 seconds should feel
like, what builds trust, what creates delight, and how emptiness and
success should be handled. Rendered into the frontend prompt as the
EXPERIENCE BLUEPRINT so the generator composes moments, not just screens.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ExperiencePlan:
    first_screen: str    # what the user must see/feel in the first 30 seconds
    trust: str           # what makes this feel dependable
    delight: str         # the one deliberate moment of joy
    empty_state: str     # how "no data yet" should feel
    success: str         # how completing the core action should feel


_EXPERIENCES: dict[str, ExperiencePlan] = {
    "fitness": ExperiencePlan(
        first_screen="Today's streak and the next workout — one glance answers "
                     "'am I on track?' before any navigation.",
        trust="Numbers that visibly update the moment an activity is logged — "
              "stats that lag feel fake.",
        delight="Completing a workout pops the streak counter with animate-pop and "
                "momentarily fills the progress ring — a physical sense of credit earned.",
        empty_state="Never a bare 'no data' — show the first goal as a ready-to-start "
                    "card ('Log your first workout') with an encouraging line.",
        success="A logged session celebrates: toast + the streak heatmap cell lighting "
                "up in the brand gradient.",
    ),
    "finance": ExperiencePlan(
        first_screen="Current balance / spend-vs-budget front and center in oversized "
                     "tight-tracking numerals — money questions get answered instantly.",
        trust="Precise formatting everywhere: currency symbols, thousands separators, "
              "red/green deltas always paired with +/- signs and labels.",
        delight="The spending chart draws in smoothly on load; staying under budget "
                "shows a quiet green 'on track' badge, not confetti.",
        empty_state="An empty ledger shows a worked example row ghosted at 40% opacity "
                    "labeled 'example' plus the add button — teaches the shape of data.",
        success="Adding a transaction updates the budget bar in the same view — cause "
                "and effect visible without navigation.",
    ),
    "productivity": ExperiencePlan(
        first_screen="Today's list, already prioritized — the app opens on what to do "
                     "next, not on a marketing-style overview.",
        trust="Instant interactions: checking off a task strikes it through immediately, "
              "counts update everywhere at once.",
        delight="Completing the last task of the day earns a single satisfying moment — "
                "an animate-pop check and a 'day cleared' line, nothing louder.",
        empty_state="An empty board is an invitation: a keyboard-hinted quick-add "
                    "('Press N or click to add your first task') centered where the list will live.",
        success="Task completion feels physical — checkbox pops, row settles out, "
                "progress ring ticks up.",
    ),
    "social": ExperiencePlan(
        first_screen="A living feed with avatars and fresh timestamps — the immediate "
                     "sense that people are here.",
        trust="Real names, consistent avatar treatment, and honest relative timestamps "
              "('2m ago') everywhere.",
        delight="Likes respond with a heart pop under the cursor; new posts slide into "
                "the top of the feed rather than appearing on refresh.",
        empty_state="'Your feed is waiting' with 2-3 suggested people/topics as ready "
                    "cards — emptiness becomes a first action.",
        success="Publishing a post inserts it at the top with the entrance animation — "
                "instant proof it exists.",
    ),
    "crm": ExperiencePlan(
        first_screen="The pipeline board with deal values visible — a rep sees the "
                     "quarter's shape in three seconds.",
        trust="Dense but ordered: consistent column rhythm, right-aligned currency, "
              "every deal card shaped identically.",
        delight="Moving a deal to Closed-Won gives one professional beat of celebration — "
                "a brief gradient shimmer on the card, no confetti.",
        empty_state="An empty pipeline shows the stage columns anyway with a ghosted "
                    "example card in stage one — the mental model precedes the data.",
        success="Logged activity appears at the top of the contact timeline immediately, "
                "timestamped.",
    ),
    "booking": ExperiencePlan(
        first_screen="Today's schedule and the next available slot — both the customer's "
                     "and the operator's core question answered at once.",
        trust="Unambiguous slot states: open/booked/unavailable each get a distinct "
              "treatment with a text label, never color alone.",
        delight="Selecting a slot confirms with a smooth scale-in summary card showing "
                "exactly what was chosen before committing.",
        empty_state="An empty calendar highlights the soonest bookable slot with a "
                    "gentle pulse rather than showing a blank grid.",
        success="A confirmed booking renders a ticket-like confirmation card — something "
                "that feels keepable.",
    ),
    "ecommerce": ExperiencePlan(
        first_screen="Products, imagery-first, above everything else — the catalog is "
                     "the hero, chrome stays out of the way.",
        trust="Stock states, prices, and order statuses always explicit; the order "
              "timeline (Placed → Shipped → Delivered) reads at a glance.",
        delight="Add-to-cart animates the item toward the cart icon and bumps its badge "
                "count with a pop.",
        empty_state="An empty cart suggests 2-3 real products as cards, not just a sad icon.",
        success="Order placed = full-width confirmation with the order timeline started, "
                "first stage already lit.",
    ),
    "healthcare": ExperiencePlan(
        first_screen="The next appointment and any items needing attention — calm "
                     "priority, zero decoration competing with information.",
        trust="Everything labeled in plain language; status colors always accompanied "
              "by text; generous spacing that never feels rushed.",
        delight="Delight here IS calm: soft transitions, nothing bounces, confirmation "
                "language is warm ('You're all set for Tuesday').",
        empty_state="'No upcoming appointments' pairs with the booking action and the "
                    "office contact info — an empty state that still helps.",
        success="Booking/updating confirms with a quiet check and a summary the patient "
                "could screenshot.",
    ),
    "ai_saas": ExperiencePlan(
        first_screen="The input — prompt box or command surface — centered and ready, "
                     "with recent activity ghosted behind it. The tool invites use immediately.",
        trust="Visible system state: streaming output renders progressively, usage "
              "meters are honest, latency is acknowledged with skeletons not spinners.",
        delight="Responses stream in with a subtle cursor shimmer; the command palette "
                "(Cmd+K) makes power users feel at home in seconds.",
        empty_state="First-run shows 3 example prompts as clickable cards — the fastest "
                    "possible path to the first wow.",
        success="A completed generation settles into the history list with a gentle "
                "scale-in — accumulating value, visibly.",
    ),
    "restaurant": ExperiencePlan(
        first_screen="Food photography and tonight's availability — appetite first, "
                     "logistics one tap later.",
        trust="Real menu prices, honest availability, warm but legible type over imagery "
              "(always with a contrast overlay).",
        delight="Menu cards lift softly on hover with the image gently scaling inside its "
                "frame — tactile, appetizing browsing.",
        empty_state="No reservations yet? Show tonight's open tables as inviting cards "
                    "with times, not an empty table list.",
        success="A reservation confirms like a place card: date, time, party size in "
                "elegant type worth screenshotting.",
    ),
    "travel": ExperiencePlan(
        first_screen="One large, beautiful destination image with the trip's next moment "
                     "(countdown, next stop) overlaid — wanderlust before admin.",
        trust="Dates, places, and bookings organized into an unambiguous day-by-day "
              "timeline that always answers 'then what?'.",
        delight="Scrolling the itinerary reveals each day with a gentle stagger; adding a "
                "stop drops a pin into the day with a small settle animation.",
        empty_state="An empty trip list is an invitation: 'Where to next?' over a muted "
                    "map-toned backdrop with the create action as the hero.",
        success="Completing a plan shows the full itinerary as a shareable-feeling "
                "summary — the payoff for organizing.",
    ),
    "education": ExperiencePlan(
        first_screen="'Continue where you left off' — the current lesson with a visible "
                     "progress bar beats any dashboard of statistics.",
        trust="Progress is always honest and granular (3 of 8 modules), with clear next "
              "steps — learners must never wonder what to do next.",
        delight="Completing a lesson fills the module's progress segment with a smooth "
                "sweep and may unlock a badge with animate-pop.",
        empty_state="No courses yet = a friendly starter path suggestion, framed as the "
                    "first step rather than a missing feature.",
        success="Quiz results celebrate effort: score counts up from zero, passed modules "
                "get their checkmark drawn in.",
    ),
    "portfolio": ExperiencePlan(
        first_screen="The owner's name in confident display type and one flagship piece — "
                     "taste communicated in a single viewport before any scrolling.",
        trust="Restraint: consistent image treatment, generous margins, no widget clutter — "
              "the grid itself demonstrates competence.",
        delight="Work reveals on scroll with a subtle editorial fade; hovering a piece "
                "shows title/role in a quiet caption, never a loud overlay.",
        empty_state="An empty gallery becomes a statement: oversized 'Work in progress' "
                    "typography treated as intentional design.",
        success="Publishing a piece slots it into the grid with a soft scale-in — the "
                "portfolio visibly grows.",
    ),
}

_DEFAULT = _EXPERIENCES["productivity"]


def compose_experience(category_key: str) -> ExperiencePlan:
    return _EXPERIENCES.get(category_key, _DEFAULT)
