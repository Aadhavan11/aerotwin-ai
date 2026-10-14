"""The copilot can see the screen: questions about the page, the aircraft on screen and the selection are answered from the ScreenContext."""
from app.contracts.models import ChatRequest, ScreenContext
from app.copilot import screen
from app.copilot.chat import chat

AIRCRAFT_PAGE = ScreenContext(page="Aircraft twin", path="/aircraft/SIM-017", role="Pilot / flight crew", tail="SIM-017",
                              selected="Engine 2 Alert", sim_now="2026-10-13T12:00:00Z", text="SIM-017 twin Alert Engine 2 health 23%")
FLEET_PAGE = ScreenContext(page="Fleet overview", path="/", role="Operations control")


def test_tail_number_comes_from_the_screen():
    text, ev = screen.answer("what is the tail number?", AIRCRAFT_PAGE)
    assert "Aircraft twin" in text and "SIM-017" in text
    assert ev[0].kind == "event" and ev[0].ref == "screen"


def test_page_selection_role_and_clock():
    assert "Aircraft twin" in screen.answer("what page am I on?", AIRCRAFT_PAGE)[0]
    assert "Engine 2" in screen.answer("which engine is selected?", AIRCRAFT_PAGE)[0]
    assert "Pilot" in screen.answer("what's my role?", AIRCRAFT_PAGE)[0]
    assert "2026-10-13" in screen.answer("what is the simulation time?", AIRCRAFT_PAGE)[0]


def test_a_page_about_no_single_aircraft_says_so():
    text, _ = screen.answer("what is the tail number?", FLEET_PAGE)
    assert "not about a single aircraft" in text


def test_fleet_questions_are_not_screen_questions():
    for q in ["which aircraft needs attention?", "Why is SIM-017 prioritized over SIM-012?",
              "which aircraft are most likely to need maintenance in 72 hours"]:
        assert screen.answer(q, AIRCRAFT_PAGE) is None, q


def test_without_a_screen_nothing_changes():
    assert screen.answer("what is the tail number?", None) is None


def test_chat_endpoint_model_accepts_and_answers_with_the_screen():
    resp = chat(ChatRequest(session_id="t-screen", message="what is the tail number?", screen=AIRCRAFT_PAGE), mode="scripted")
    assert "SIM-017" in resp.answer and "Aircraft twin" in resp.answer


def test_describe_carries_what_the_llm_needs():
    block = screen.describe(AIRCRAFT_PAGE)
    assert "SIM-017" in block and "Pilot / flight crew" in block and "Engine 2 Alert" in block and "this aircraft" in block.lower()
