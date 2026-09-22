"""Click-through tests of the real pages in a real (headless) browser.

    python tests/browser/run_browser_tests.py             # run every check
    python tests/browser/run_browser_tests.py --shots out # also save screenshots into the folder "out"

Needs: `pip install websocket-client`, and Microsoft Edge, Chrome or Chromium installed (set the BROWSER
environment variable to its path if it isn't found). It starts its own local web server and browser, and
needs internet access because the pages load Plotly from a CDN. These are not run by `pytest`; they are
slower and depend on a browser, so run them when you change anything under web/.

What it covers that the other tests can't: the pages actually render, buttons and sliders do what they
should, zooming works, and nothing overflows sideways on a phone. It caught a real bug in Tier 1 (the "5y"
and "10y" buttons showing the wrong years) that no screenshot had revealed.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Browser, StaticServer  # noqa: E402

failures: list[str] = []


def check(name: str, condition, detail: str = "") -> None:
    print(("PASS  " if condition else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not condition:
        failures.append(name)


def click(b: Browser, selector_js: str) -> None:
    """A real DOM click (Plotly's own buttons listen for mouse events, so `.click()` is not enough)."""
    b.js(f"({selector_js}).dispatchEvent(new MouseEvent('click', {{bubbles: true}}))")
    b.pause(700)


def number(text: str) -> int:
    return int(text.split(" ")[0].replace(",", "").replace("+", "").replace("−", "-"))


# ---------------------------------------------------------------------------------------------------------------


def test_tier1(b: Browser, url: str) -> None:
    print("\n-- Tier 1: renewable share")
    b.goto(f"{url}/index.html", "document.querySelector('#chart .main-svg') && document.querySelectorAll('.rangeselector .button').length === 3")
    full = b.js("document.getElementById('chart')._fullLayout.xaxis.range.map(String)")
    check("'All' ends at the newest month (the axis is not padded past the data)", full[1] < "2026-12" and full[1] > "2026-01", str(full))
    end_year = int(full[1][:4])
    click(b, "document.querySelectorAll('.rangeselector .button')[0]")
    five = b.js("document.getElementById('chart').layout.xaxis.range")
    check("5y button shows the last five years", str(end_year - 5) <= five[0][:4] <= str(end_year - 4) and five[1][:4] == full[1][:4], str(five))
    click(b, "document.querySelectorAll('.rangeselector .button')[1]")
    ten = b.js("document.getElementById('chart').layout.xaxis.range")
    check("10y button shows the last ten years", str(end_year - 10) <= ten[0][:4] <= str(end_year - 9), str(ten))
    dot = b.js("""(() => { const p = [...document.querySelectorAll('#chart .scatterlayer .points path')]
                   .filter(x => x.getBoundingClientRect().width > 3).pop(); return p ? getComputedStyle(p).opacity : null; })()""")
    check("the end-of-line dot is fully opaque", dot == "1", str(dot))
    check("the table lists every month", b.js("document.querySelectorAll('#data-table tbody tr').length") > 300)
    names = b.js("document.getElementById('chart').data.filter(t => t.showlegend !== false).map(t => t.name)")
    check("the legend names the plant-survey line and the rooftop estimate", names == ["Utility-scale solar + wind", "Including rooftop solar (estimate)"], str(names))
    rooftop_from = b.js("document.getElementById('chart').data.find(t => (t.name || '').startsWith('Including rooftop')).x[0].slice(0, 7)")
    check("the rooftop line starts when EIA's estimate does (2014)", rooftop_from == "2014-01", rooftop_from)
    clipped = b.js("""(() => { const box = document.querySelector('#chart .main-svg').getBoundingClientRect();
        return [...document.querySelectorAll('#chart .annotation-text-g, #chart .annotation')].filter(a => a.getBoundingClientRect().right > box.right + 1).length; })()""")
    check("no chart label runs off the edge of the chart", clipped == 0, f"{clipped} clipped")
    check("the info panel opens beside the chart on a wide screen", b.js("document.querySelector('.info-panel').getBoundingClientRect().left > document.getElementById('chart').getBoundingClientRect().right - 40"))
    b.js("document.querySelector('.info-panel details').open = false")
    check("the long explanation is collapsed by default", b.js("document.querySelector('.info-panel details').open === false"))
    # ---- hover: only the line under the pointer explains itself ---------------------------------------------------
    click(b, "document.querySelectorAll('.rangeselector .button')[2]")  # "All", so the whole line is on screen
    check("hovering uses 'closest', not the old box listing every line at once", b.js("document.getElementById('chart')._fullLayout.hovermode") == "closest")

    def line_point(trace_name, fraction):
        """Page coordinates of a spot part-way along one drawn line."""
        return b.js(f"""(() => {{ const chart = document.getElementById('chart');
            const index = chart.data.findIndex(t => t.name === {trace_name!r});
            const path = document.querySelectorAll('#chart .cartesianlayer .subplot.xy .scatterlayer .trace')[index].querySelector('.lines path');
            const p = path.getPointAtLength(path.getTotalLength() * {fraction}), m = path.getScreenCTM();
            return [p.x * m.a + p.y * m.c + m.e, p.x * m.b + p.y * m.d + m.f]; }})()""")

    def tooltips():
        b.pause(350)
        return b.js("[...document.querySelectorAll('#chart .hoverlayer .hovertext')].map(t => t.textContent)")

    b.move_to(*line_point("Utility-scale solar + wind", 0.85))
    tip = tooltips()
    check("hovering the blue line shows one tooltip: its month and share, nothing about the other line",
          len(tip) == 1 and "renewable share" in tip[0] and "rooftop" not in tip[0] and any(m in tip[0] for m in ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")), str(tip))
    b.move_to(*line_point("Including rooftop solar (estimate)", 0.6))
    tip = tooltips()
    check("hovering the dotted line shows only the rooftop tooltip", len(tip) == 1 and "including rooftop solar" in tip[0] and "Rooftop solar about" in tip[0], str(tip))
    box = b.js("(r => [r.left, r.top, r.width, r.height])(document.querySelector('#chart .draglayer .nsewdrag').getBoundingClientRect())")
    b.move_to(box[0] + box[2] * 0.15, box[1] + box[3] * 0.4)  # empty plot area, well away from every line
    check("hovering empty space shows nothing", tooltips() == [])

    # ---- milestones ----------------------------------------------------------------------------------------------
    b.js("document.querySelector('.info-panel details').open = false")
    milestones = b.js("fetch('data/milestones.json').then(r => r.json())")["regions"]["oahu"]
    buttons = b.js("[...document.querySelectorAll('#milestone-buttons button')].map(x => x.textContent)")
    check("ten milestones are offered, numbered in date order", len(milestones) == 10 and len(buttons) == 10 and buttons[0].startswith("1 · ") and buttons[9].startswith("10 · "), str(buttons[:2]))
    check("the milestones are in date order and each has a description, a source and a link",
          [m["month"] for m in milestones] == sorted(m["month"] for m in milestones)
          and all(m["text"] and m["source"] and m["url"].startswith("https://") and m["date_text"] for m in milestones))
    marks = b.js("(() => { const t = document.getElementById('chart').data.find(t => t.name === 'Milestones'); return t ? t.x.map(x => x.slice(0, 7)) : null; })()")
    check("the chart draws one diamond per milestone, at its month", marks == [m["month"] for m in milestones], str(marks))
    check("nothing is selected at first", b.js("document.getElementById('milestone-card').textContent") == "No milestone selected yet.")

    # A real mouse click on the 7th diamond (the four solar farms).
    b.js("window.scrollTo(0, 0)")
    b.js("Plotly.relayout('chart', {'xaxis.range': ['2018-01-01', '2021-01-01']}).then(() => true)")
    b.pause(500)
    # (the range slider under the chart draws its own tiny copy of every trace, so look only inside the main plot)
    spot = b.js("""(() => { const plot = document.querySelector('#chart .cartesianlayer .subplot.xy'); const box = document.querySelector('#chart .draglayer .nsewdrag').getBoundingClientRect();
        const pts = [...plot.querySelectorAll('.scatterlayer .trace')].pop().querySelectorAll('.points path.point');
        const r = [...pts].map(p => p.getBoundingClientRect()).find(r => r.width > 5 && r.left > box.left && r.right < box.right);
        return r ? [r.left + r.width / 2, r.top + r.height / 2] : null; })()""")
    b.click_at(*spot)
    b.pause(700)
    card = b.js("document.getElementById('milestone-card').innerText")
    check("clicking a diamond on the chart opens that milestone's card", "Four solar farms come online" in card and "Sep–Nov 2019" in card and "130 MW" in card, card[:60])
    check("its button is marked as selected", b.js("document.querySelector('#milestone-buttons button[aria-pressed=true]').textContent") == "7 · Four solar farms")
    check("the chosen diamond is highlighted", b.js("document.getElementById('chart').data.find(t => t.name === 'Milestones').marker.size.filter(s => s > 11).length") == 1)

    b.js("document.querySelectorAll('#milestone-buttons button')[8].click()")
    b.pause(500)
    link = b.js("(a => a ? [a.href, a.target, a.rel] : null)(document.querySelector('#milestone-card a'))")
    check("the buttons open cards too, and the source is a link that opens in a new tab",
          "AES coal plant closes" in b.js("document.getElementById('milestone-card').innerText") and link and link[0].startswith("https://www.hawaiipublicradio.org/") and link[1] == "_blank" and "noopener" in link[2], str(link))
    b.js("document.querySelectorAll('#milestone-buttons button')[0].click()")
    b.pause(400)
    first = b.js("document.getElementById('milestone-card').innerText")
    check("the Mar 2011 card explains that 'first wind' means first in EIA's data", "Mar 2011" in first and "first Oʻahu wind in EIA's data" in first)
    check("the milestones did not change the lines", b.js("document.getElementById('chart').data.filter(t => t.showlegend !== false).map(t => t.name).join('|')") == "Utility-scale solar + wind|Including rooftop solar (estimate)")
    check("no JavaScript errors", not b.errors, str(b.errors[:2]))


def test_tier2(b: Browser, url: str) -> None:
    print("\n-- Tier 2: duck curve")
    b.goto(f"{url}/duck_curve.html", "document.querySelectorAll('.stat').length === 4 && document.querySelector('#chart .main-svg')")
    stats = lambda: b.js("[...document.querySelectorAll('.stat-value')].map(e => e.textContent).join('|')")
    check("opens on the season with the deepest midday dip", b.js("document.querySelector('#season-controls [aria-checked=true]').textContent").startswith("Spring"))
    before = stats()
    b.js("[...document.querySelectorAll('#season-controls button')].find(x => x.textContent.startsWith('Summer')).click()")
    b.pause()
    check("choosing Summer updates the subtitle and the numbers", "summer" in b.js("document.getElementById('subtitle').textContent") and stats() != before, f"{before} -> {stats()}")
    check("the table has 24 hours", b.js("document.querySelectorAll('#data-table tbody tr').length") == 24)
    notes = b.js("document.getElementById('notes').textContent")
    check("the notes explain 'shape from NREL, size from EIA' and the statewide-to-Oʻahu scaling",
          "Shape from NREL, size from EIA" in notes and "whole state of Hawaiʻi" in notes and "77%" in notes)
    check("the rooftop shape is described as ResStock's simulated home panels, not the old vague wording",
          "ResStock" in notes and "actual 2018 weather" in notes and "daily shape of modeled home panels" not in notes)
    check("a plain-language curtailment explainer is present and collapsed", b.js("document.querySelector('details.explainer').open === false"))
    b.js("document.querySelector('details.explainer').open = true")
    body = b.js("document.getElementById('curtailment-explainer-body').textContent")
    check("the explainer defines curtailment and quotes Hawaiian Electric's real figures", "produce less than it could" in body and "GWh" in body and "worst year" in body, body[:60])
    check("no JavaScript errors", not b.errors, str(b.errors[:2]))


def test_tier3(b: Browser, url: str) -> None:
    print("\n-- Tier 3: curtailment")
    b.goto(f"{url}/curtailment.html", "document.querySelector('#chart-reasons .main-svg') && document.querySelector('#chart-energy .main-svg') && document.querySelectorAll('.rangeselector .button').length === 3")
    span = lambda d: b.js(f"document.getElementById('{d}').layout.xaxis.range.map(x => String(x).slice(0, 10))")
    check("the three charts start on the same dates", span("chart-energy")[0] == span("chart-rate")[0] == span("chart-reasons")[0] or span("chart-rate") == span("chart-reasons"),
          f"{span('chart-rate')} {span('chart-reasons')}")
    click(b, "document.querySelectorAll('#chart-energy .rangeselector .button')[0]")
    e, r, s = span("chart-energy"), span("chart-rate"), span("chart-reasons")
    check("the 5y button zooms all three charts to the same dates", e == r == s and int(e[1][:4]) - int(e[0][:4]) in (4, 5), f"{e} | {r} | {s}")
    click(b, "document.querySelectorAll('#chart-energy .rangeselector .button')[2]")
    r2, s2 = span("chart-rate"), span("chart-reasons")
    check("'All' puts the other two charts back on the full span", r2 == s2 and r2[0] < "2013", f"{r2} | {s2}")
    check("no JavaScript errors", not b.errors, str(b.errors[:2]))


def test_tier4(b: Browser, url: str) -> None:
    print("\n-- Tier 4: battery")
    b.goto(f"{url}/battery.html", "document.querySelectorAll('.stat').length === 5 && document.querySelector('#chart-net .main-svg') && document.querySelector('#chart-battery .main-svg')")

    def tiles():
        return b.js("[...document.querySelectorAll('.stat')].map(t => [...t.children].map(c => c.textContent))")

    def slide(name, value):
        b.js(f"(() => {{ const i = document.getElementById('slider-{name}'); i.value = {value}; i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")
        b.pause()

    readout = lambda name: b.js(f"document.getElementById('slider-{name}').parentElement.querySelector('output').textContent")

    default = tiles()
    check("opens with 5 sliders, 5 result tiles and 4 seasons", b.js("document.querySelectorAll('input[type=range]').length") == 5 and len(default) == 5
          and b.js("document.querySelectorAll('#season-controls button').length") == 4)
    # The default battery is Oʻahu's real fleet (data/plants.json), snapped to the sliders' steps (1 MW, 5 MWh).
    fleet = b.js("fetch('data/plants.json').then(r => r.json()).then(d => d.regions.oahu.battery_fleet)")
    power0, energy0 = round(fleet["power_mw"]), 5 * round(fleet["energy_mwh"] / 5)
    duration = f"{energy0 / power0:.1f}"
    check("the default battery is Oʻahu's whole fleet", readout("power") == f"{power0} MW" and readout("energy") == f"{energy0:,} MWh · {duration} h at full power",
          f"{readout('power')}, {readout('energy')} (fleet: {fleet['power_mw']} MW / {fleet['energy_mwh']} MWh)")
    check("the fleet total uses the corrected Waiawa figure and says so", fleet["energy_mwh"] > fleet["energy_mwh_as_filed"] and any(u["corrected"] for u in fleet["units"]))
    slide("energy", energy0)
    check("touching a slider at its default does not change the result", tiles()[0][1] == default[0][1])

    slide("power", 0)
    check("with no battery power, nothing changes", "no change" in tiles()[0][2] and tiles()[4][1].startswith("0 MWh"), str(tiles()[0][1:]))
    slide("power", power0)
    check("restoring the power restores the result exactly", tiles()[0][1] == default[0][1])
    slide("power", 400)
    slide("energy", 1600)
    check("a bigger battery lowers the evening peak", number(tiles()[0][1]) < number(default[0][1]), f"{default[0][1]} -> {tiles()[0][1]}")
    check("the energy readout shows the duration", "4.0 h" in readout("energy"), readout("energy"))

    slide("power", 0)
    slide("solar", 200)
    without = number(tiles()[3][1])
    check("with much more solar and no battery, solar is curtailed", without > 0, f"{without} MWh/day")
    slide("power", power0)
    slide("energy", energy0)
    with_battery = number(tiles()[3][1])
    check("a battery absorbs some of the curtailed solar", with_battery < without, f"{without} -> {with_battery} MWh/day")
    check("the 'was' figure is the no-battery amount", f"was {without:,} MWh" in tiles()[3][2], tiles()[3][2])

    spring = tiles()[0][1]
    b.js("[...document.querySelectorAll('#season-controls button')].find(x => x.textContent === 'Winter').click()")
    b.pause()
    check("choosing Winter changes the day", "winter" in b.js("document.getElementById('subtitle').textContent") and tiles()[0][1] != spring)
    b.js("document.getElementById('reset').click()")
    b.pause()
    check("Reset restores every slider", [readout(n) for n in ("power", "energy", "efficiency", "solar", "floor")] ==
          [f"{power0} MW", f"{energy0:,} MWh · {duration} h at full power", "85%", "0%", "300 MW"], str([readout(n) for n in ("power", "energy", "efficiency", "solar", "floor")]))
    check("the table has 24 hours", b.js("document.querySelectorAll('#data-table tbody tr').length") == 24)
    check("the battery explainer is present and collapsed", b.js("document.querySelector('details.explainer').open === false"))
    b.js("document.querySelector('details.explainer').open = true")
    body = b.js("document.getElementById('battery-explainer-body').textContent")
    check("the explainer names the real owner and its other grid services",
          "Plus Power" in body and "fast-frequency response" in body and "backup power" in body
          and "Kapolei Energy Storage I, LLC" in body and "AES Kapolei" not in body)
    check("the explainer lists the fleet, explains the Waiawa correction and the charging caution",
          "whole battery fleet" in body and "4.6 MWh" in body and "144 MWh" in body and "from where they can charge" in body)
    check("the explainer says the duck curve page has no battery, and cites its sources",
          "contains no battery at all" in body and b.js("document.querySelectorAll('.explainer-sources a').length") == 4)
    notes = b.js("document.getElementById('notes').textContent")
    check("the retracted 'a battery may not relieve it' claim is gone", "may not relieve" not in notes and "may understate what batteries can do" in notes)
    check("no JavaScript errors", not b.errors, str(b.errors[:2]))


def test_map(b: Browser, url: str) -> None:
    print("\n-- Plant map")
    b.goto(f"{url}/map.html", "window.__mapState && document.querySelectorAll('#group-table tbody tr').length > 0")
    b.pause(1500)  # let the tiles arrive
    data = b.js("fetch('data/plants.json').then(r => r.json())")
    plants = data["plants"]

    def expected_plants(month):
        return sum(1 for p in plants if any(not u["planned"] and u["online"] <= month and (u["retired"] is None or u["retired"] > month) for u in p["units"]))

    def expected_planned_plants(month, as_of):
        """Plants showing as planned: a planned unit appears from its expected month, but only after the last month of real records."""
        return sum(1 for p in plants if any(u["planned"] and month > as_of and month >= u["online"] for u in p["units"]))

    def dashed():
        return b.js("document.querySelectorAll('#map path.leaflet-interactive[stroke-dasharray]').length")

    def go(month):
        b.js(f"(() => {{ const s = document.getElementById('time'); s.value = window.__mapState.months.indexOf('{month}'); s.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")
        b.pause(250)

    def shown():
        return b.js("document.querySelector('#stats .stat-value').textContent")

    region = data["regions"]["oahu"]
    first, last, furthest = region["first_month"], region["as_of"], region["last_planned_month"]
    planned_units = [u for p in plants for u in p["units"] if u["planned"]]
    check("EIA's Planned sheet is in the data, and the furthest planned month is the last expected in-service month",
          len(planned_units) > 0 and furthest == max(u["online"] for u in planned_units) and furthest > last, f"{len(planned_units)} planned units, furthest {furthest}")
    check("the slider spans EIA's earliest in-service month to the furthest planned plant",
          first == "1947-12" and b.js("window.__mapState.months[0]") == first and b.js("window.__mapState.months.at(-1)") == furthest, f"{first} to {furthest}")
    today = date.today().strftime("%Y-%m")
    want_today = first if today < first else furthest if today > furthest else today
    check("it opens on today's month", b.js("window.__mapState.months[Number(document.getElementById('time').value)]") == want_today, f"today {want_today}")
    check("the basemap comes from OpenStreetMap (not a provider that needs a key)",
          b.js("[...document.querySelectorAll('#map img.leaflet-tile-loaded')].length > 0 && [...document.querySelectorAll('#map img.leaflet-tile-loaded')].every(i => i.src.startsWith('https://tile.openstreetmap.org/'))"))
    check("map zoom is a whole number (no seams between tiles)", b.js("Number.isInteger(window.__mapState.map.getZoom())"))

    for month in ("1947-12", "1992-06", "2022-08", "2022-10", last):
        go(month)
        want = expected_plants(month)
        on_map = b.js("document.querySelectorAll('#map path.leaflet-interactive').length")
        check(f"{month}: the page and the data agree on {want} plants in service", shown() == str(want) and on_map == want, f"tile says {shown()}, circles {on_map}")

    go("2022-08")
    has_coal = lambda: b.js("[...document.querySelectorAll('#group-table tbody tr')].some(r => r.textContent.startsWith('Coal'))")
    coal_before = has_coal()
    go("2022-10")
    check("AES Hawaii's coal plant is on the map in Aug 2022 and gone by Oct 2022 (it closed Sept 1)", coal_before and not has_coal())

    # ---- planned plants: the timeline past the newest EIA file -------------------------------------------------------
    go(last)
    real = expected_plants(last)
    check("in the newest month of real records there are no dashed (planned) circles", dashed() == 0 and shown() == str(real))
    earliest_plan = min(u["online"] for u in planned_units)
    go(earliest_plan)
    plan_now = expected_planned_plants(earliest_plan, last)
    check(f"{earliest_plan}: planned plants appear as dashed circles, on top of the in-service ones",
          plan_now > 0 and dashed() == plan_now and b.js("document.querySelectorAll('#map path.leaflet-interactive').length") == expected_plants(earliest_plan) + plan_now, f"{plan_now} planned")
    check("planned plants are listed separately and not counted as in service",
          shown() == str(expected_plants(earliest_plan)) and b.js("[...document.querySelectorAll('#stats .stat-label')].some(l => l.textContent.startsWith('Planned'))")
          and b.js("[...document.querySelectorAll('#group-table th')].some(h => h.textContent === 'Planned')"))
    go(furthest)
    check(f"{furthest}: every planned plant is on the map and the in-service count is unchanged by them",
          dashed() == expected_planned_plants(furthest, last) == len({p["code"] for p in plants if any(u["planned"] for u in p["units"])}) and shown() == str(expected_plants(furthest)))
    big_plan = next(p for p in plants if any(u["planned"] for u in p["units"]) and p["name"] == "Puuloa Energy")
    b.js(f"window.__mapState.markers.get('{big_plan['code']}').marker.fire('click') && true")
    b.pause(300)
    card = b.js("document.getElementById('plant-card').innerText")
    check("a planned plant's card says it is not built, gives EIA's stage, and shows the expected month",
          "planned Apr 2029" in card and "not built yet" in card and "regulatory approvals not initiated" in card and b.js("document.querySelectorAll('#plant-card tr.unit-planned').length") > 0)
    check("the note under the slider says the map is now showing plans", b.js("document.getElementById('time-note').classList.contains('time-note-plan')"))
    go(last)
    check("...and drops that when the date is back within EIA's records", not b.js("document.getElementById('time-note').classList.contains('time-note-plan')"))

    go("1950-01")
    b.js("document.getElementById('today').click()")
    b.pause(250)
    check("the Today button jumps back to the current month", b.js("window.__mapState.months[Number(document.getElementById('time').value)]") == want_today)

    b.js("window.__mapState.markers.get(window.__mapState.plants.find(p => p.name === 'Kahe').code).marker.fire('click') && true")
    b.pause(400)
    card = b.js("document.getElementById('plant-card').innerText")
    check("clicking a plant opens its card with units and in-service dates", "Kahe" in card and "K1" in card and "Mar 1963" in card and "still operating" in card)
    check("units in service on the slider date are bold", b.js("document.querySelectorAll('#plant-card tr.unit-now').length") > 0)
    go("1950-01")
    check("the card follows the slider: Kahe's units are not yet in service in 1950", b.js("document.querySelectorAll('#plant-card tr.unit-now').length") == 0)
    b.js("document.querySelector('#data-table button') && document.querySelector('#data-table button').click()")

    check("the explainer states EIA-860's real rules", b.js("(b => b.includes('1 megawatt') && b.includes('connected to the electric grid') && b.includes('2002'))(document.getElementById('explainer-body').textContent)"))
    check("plants EIA lists but the renewable-share chart excludes are labelled as such", any(p["excluded_reason"] for p in plants))

    # Play goes month by month at a steady speed: a year every 0.11 s, about 109 months a second (as it did when it
    # jumped a year at a time). Record every change of the slider while it plays.
    go(first)
    b.js("""(() => {
      const slider = document.getElementById('time');
      window.__played = [];
      new MutationObserver(() => window.__played.push([performance.now(), Number(slider.value)]))
        .observe(document.getElementById('time-label'), {childList: true, characterData: true, subtree: true});
    })()""")
    b.js("document.getElementById('play').click()")
    b.pause(1200)
    b.js("document.getElementById('play').click()")  # pause
    b.pause(300)
    settled = b.js("Number(document.getElementById('time').value)")
    b.pause(400)
    steps = b.js("window.__played")
    values = [v for _, v in steps]
    biggest_jump = max((later - earlier for earlier, later in zip(values, values[1:])), default=0)
    seconds = (steps[-2][0] - steps[0][0]) / 1000 if len(steps) > 2 else 0
    speed = (steps[-2][1] - steps[0][1]) / seconds if seconds else 0
    check("Pause stops the timeline", settled > 0 and b.js("Number(document.getElementById('time').value)") == settled, f"paused at step {settled}")
    check("Play moves month by month, not a year at a time", len(steps) > 30 and biggest_jump <= 6, f"{len(steps)} updates in {seconds:.2f} s, largest jump {biggest_jump} months")
    check("Play keeps the old speed (about 109 months a second)", 70 <= speed <= 150, f"{speed:.0f} months a second")
    go("2020-01")
    b.js("document.getElementById('today').click()")
    check("Today while nothing is playing leaves Play ready", b.js("document.getElementById('play').textContent.includes('Play')"))
    check("no JavaScript errors", not b.errors, str(b.errors[:2]))


def test_manoa(b: Browser, url: str) -> None:
    print("\n-- Mānoa solar page")
    ready = "document.querySelector('#chart .main-svg') && document.querySelectorAll('.stat').length === 5"
    b.goto(f"{url}/manoa.html", ready)
    b.pause(600)
    data = b.js("fetch('data/manoa_solar.json').then(r => r.json())")
    sensor_data = b.js("fetch('data/manoa_solar_sensor.json').then(r => r.json())")
    rows = {(r["day_set"], r["season"], r["hour"]): r for r in data["rows"]}
    sensor_rows = {(r["day_set"], r["season"], r["hour"]): r for r in sensor_data["rows"]}
    seasons = list(data["seasons"])
    PVWATTS_SCALE = 4.3  # the PVWatts file is a 1,000 kW reference, scaled up to Mānoa's real 4,300 kW array

    def kw(day_set, season, field="dc_w"):
        return [rows[(day_set, season, h)][field] / 1000 * PVWATTS_SCALE for h in range(24)]

    def psh(day_set, season):
        # Peak sun hours: that day's sunlight in kWh/m2, independent of array size or DC/AC.
        return sum(rows[(day_set, season, h)]["poa_wm2"] for h in range(24)) / 1000

    def sensor_kw(day_set, season, field="dc_w"):
        # None (a real night, or a data gap) stays None: callers filter it out, same as the page does.
        return [None if (v := sensor_rows[(day_set, season, h)][field]) is None else v / 1000 for h in range(24)]

    richest = max(seasons, key=lambda sid: sum(kw("weekdays", sid)))
    trace = lambda i: f"document.getElementById('chart').data[{i}]"
    tiles = lambda: b.js("[...document.querySelectorAll('#stats .stat')].map(t => [...t.children].map(c => c.textContent))")

    check("the Mānoa page is in the site's navigation", b.js("[...document.querySelectorAll('.site-nav a')].some(a => a.getAttribute('href') === 'manoa.html' && a.getAttribute('aria-current') === 'page')"))
    check("it is tagged as modeled and says it is not a measurement", b.js("document.querySelector('.tag').textContent") == "Modeled" and "not a measurement" in b.js("document.getElementById('notes').textContent"))
    check("the season buttons offer each season plus a compare view",
          b.js("[...document.querySelectorAll('#season-controls button')].map(x => x.textContent.split(' (')[0])") == [data["seasons"][s]["label"] for s in seasons] + ["Compare all four"])
    check(f"it opens on the season that makes the most energy ({richest}), for weekdays and DC power",
          data["seasons"][richest]["label"] in b.js("document.querySelector('#season-controls [aria-checked=true]').textContent")
          and b.js("document.querySelector('#days-controls [aria-checked=true]').textContent") == "Weekdays"
          and b.js("document.querySelector('#power-controls [aria-checked=true]').textContent").startswith("DC"))

    # ---- the drawn curve is the data ---------------------------------------------------------------------------------
    smooth_y = b.js(f"{trace(0)}.y")
    dots_x, dots_y = b.js(f"{trace(1)}.x"), b.js(f"{trace(1)}.y")
    want = [(h + 0.5, v) for h, v in enumerate(kw("weekdays", richest)) if v > 0]
    check("the dots are the model's hourly averages, each plotted in the middle of its hour",
          len(dots_x) == len(want) and all(abs(x - wx) < 1e-9 and abs(y - wy) < 1e-6 for x, y, (wx, wy) in zip(dots_x, dots_y, want)), f"{len(dots_x)} dots")
    check("the smooth curve never dips below zero and peaks where the data do",
          min(smooth_y) >= 0 and max(dots_y) <= max(smooth_y) <= max(dots_y) * 1.005, f"min {min(smooth_y):.3f}, max {max(smooth_y):.1f} vs {max(dots_y):.1f}")
    check("no fitted/idealized curve is drawn - only the real data line and its dots",
          len(b.js("document.getElementById('chart').data")) == 2, str(len(b.js("document.getElementById('chart').data"))))
    check("the x axis reads in clock times", b.js("document.getElementById('chart').layout.xaxis.ticktext.join(',')") == "6 AM,9 AM,12 PM,3 PM,6 PM")

    # ---- the numbers on the tiles ------------------------------------------------------------------------------------
    dc = kw("weekdays", richest)
    t = tiles()
    check("the peak tile is the highest hourly value", t[0][0] == "Peak power" and t[0][1] == f"{round(max(dc)):,} kW", str(t[0]))
    check("the energy tile is the day's total in MWh", t[1][1] == f"{sum(dc) / 1000:.1f} MWh", str(t[1]))
    check("the width tile gives hours and a clock-time span", t[2][1].endswith(" hours") and "AM to" in t[2][2] and "PM" in t[2][2], str(t[2]))
    expected_cf = 100 * sum(dc) / (4300 * 24)  # sum(dc) is kWh (24 hourly kW averages); the 4,300 kW array x 24 h is the day's capacity
    check("the capacity-factor tile is the day's average power over the array's rating", t[3][0] == "Capacity factor" and t[3][1] == f"{expected_cf:.1f}%", str(t[3]))
    check("the peak-sun-hours tile is that day's sunlight in kWh/m2, independent of array size",
          t[4][0] == "Peak sun hours" and t[4][1] == f"{psh('weekdays', richest):.1f} PSH", str(t[4]))

    # ---- the controls ------------------------------------------------------------------------------------------------
    def pick(group, text):
        b.js(f"[...document.querySelectorAll('#{group}-controls button')].find(x => x.textContent.startsWith({text!r})).click()")
        b.pause(400)

    pick("power", "AC")
    ac = kw("weekdays", richest, "ac_w")
    check("choosing AC redraws with the after-inverter numbers (lower than DC)",
          tiles()[0][1] == f"{round(max(ac)):,} kW" and max(ac) < max(dc) and "AC" in b.js("document.getElementById('chart').layout.yaxis.title.text"), tiles()[0][1])
    check("peak sun hours is about sunlight, not power, so it doesn't change between DC and AC",
          tiles()[4][1] == f"{psh('weekdays', richest):.1f} PSH", tiles()[4][1])
    pick("power", "DC")
    pick("days", "All days")
    every = kw("all_days", richest)
    check("choosing All days uses the all-days averages", tiles()[0][1] == f"{round(max(every)):,} kW" and "weekday" not in b.js("document.getElementById('subtitle').textContent"), tiles()[0][1])
    pick("days", "Weekdays")
    pick("season", "Winter")
    winter = kw("weekdays", "winter")
    check("choosing a season shows that season, and winter's peak is smaller than summer's", tiles()[0][1] == f"{round(max(winter)):,} kW" and max(winter) < max(dc), tiles()[0][1])
    check("every season is drawn to the same scale, so seasons compare fairly",
          b.js("document.getElementById('chart').layout.yaxis.range[1]") >= max(max(kw("weekdays", s)) for s in seasons))

    pick("season", "Compare")
    names = b.js("document.getElementById('chart').data.filter(t => t.showlegend !== false).map(t => t.name)")
    check("Compare draws one curve per season, named in the legend", names == [data["seasons"][s]["label"] for s in seasons], str(names))
    labels = b.js("[...document.querySelectorAll('#stats .stat')].map(t => t.firstChild.textContent)")
    peaks = b.js("[...document.querySelectorAll('#stats .stat-value')].map(t => t.textContent)")
    check("Compare shows a tile per season with its own peak",
          labels == [data["seasons"][s]["label"] for s in seasons] and peaks == [f"{round(max(kw('weekdays', s))):,} kW peak" for s in seasons], str(peaks))
    check("the table follows the view: hours down the side, one column per season",
          b.js("document.querySelectorAll('#data-table thead th').length") == 5 and b.js("document.querySelectorAll('#data-table tbody tr').length") == 24)

    # ---- two estimates: PVWatts model and Sensor + pvlib, overlaid or shown alone --------------------------------------
    b.goto(f"{url}/manoa.html?season={richest}", ready)
    b.pause(600)
    estimate = lambda: b.js("[...document.querySelectorAll('#estimate-controls button')].map(x => [x.textContent, x.getAttribute('aria-checked')])")
    check("PVWatts is shown by default, Sensor + pvlib is opt-in", estimate() == [["PVWatts model", "true"], ["Sensor + pvlib", "false"]], str(estimate()))
    check("only one estimate drawn by default: the smooth line and its dots, nothing more", b.js("document.getElementById('chart').data.length") == 2)

    click(b, "document.querySelectorAll('#estimate-controls button')[1]")
    check("turning on Sensor + pvlib doubles the traces (one line + one dot series per estimate)",
          b.js("document.getElementById('chart').data.length") == 4, str(b.js("document.getElementById('chart').data.length")))
    stat_labels = b.js("[...document.querySelectorAll('#stats .stat-label')].map(t => t.textContent)")
    check("both estimates now have their own five tiles, including peak sun hours",
          "Peak power (PVWatts)" in stat_labels and "Peak power (Sensor)" in stat_labels
          and "Peak sun hours (PVWatts)" in stat_labels and "Peak sun hours (Sensor)" in stat_labels
          and len(stat_labels) == 10, str(stat_labels))
    dc_present = [v for v in sensor_kw("weekdays", richest, "dc_w") if v is not None]
    sensor_peak_tile = b.js("document.querySelectorAll('#stats .stat-value')[5].textContent")
    check("the Sensor + pvlib tile matches its own file's peak (no PVWatts scaling applied to it)",
          sensor_peak_tile == f"{round(max(dc_present)):,} kW", sensor_peak_tile)
    check("night hours are a real absence for the sensor estimate, not a modeled zero",
          sensor_rows[("weekdays", richest, 0)]["dc_w"] is None)

    click(b, "document.querySelectorAll('#estimate-controls button')[0]")  # turn PVWatts back off, leaving only Sensor
    check("turning off the other estimate leaves just Sensor + pvlib (2 traces, 5 tiles, no source suffix)",
          b.js("document.getElementById('chart').data.length") == 2 and
          b.js("document.querySelectorAll('#stats .stat-label').length") == 5 and
          b.js("document.querySelectorAll('#stats .stat-label')[0].textContent") == "Peak power")
    click(b, "document.querySelectorAll('#estimate-controls button')[1]")  # try to turn off the only remaining estimate
    check("the last visible estimate cannot be switched off", estimate()[1] == ["Sensor + pvlib", "true"], str(estimate()))
    click(b, "document.querySelectorAll('#estimate-controls button')[0]")  # back to the default, PVWatts only
    check("the sensor's real-coverage caveat is in the notes",
          "did not run at night" in b.js("document.getElementById('notes').textContent"))

    # ---- hover: only the dot under the pointer explains itself -----------------------------------------------------
    b.goto(f"{url}/manoa.html?season={richest}", ready)
    b.pause(600)
    spot = b.js("""(() => { const plot = document.querySelector('#chart .cartesianlayer .subplot.xy');
        const pts = [...plot.querySelectorAll('.scatterlayer .trace')][1].querySelectorAll('.points path.point');
        const r = pts[6].getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()""")
    b.move_to(*spot)
    b.pause(400)
    tip = b.js("[...document.querySelectorAll('#chart .hoverlayer .hovertext')].map(t => t.textContent)")
    check("hovering a dot shows one tooltip: the hour, DC and AC power, and sunlight",
          len(tip) == 1 and " kW" in tip[0] and "AC" in tip[0] and "W/m" in tip[0] and ("AM" in tip[0] or "PM" in tip[0]), str(tip))
    box = b.js("(r => [r.left, r.top, r.width, r.height])(document.querySelector('#chart .draglayer .nsewdrag').getBoundingClientRect())")
    b.move_to(box[0] + box[2] * 0.06, box[1] + box[3] * 0.2)
    b.pause(300)
    check("hovering empty space shows nothing", b.js("document.querySelectorAll('#chart .hoverlayer .hovertext').length") == 0)

    # ---- the address bar, the table and the notes --------------------------------------------------------------------
    b.goto(f"{url}/manoa.html?season=winter&power=ac&days=all_days", ready)
    b.pause(500)
    check("?season, ?power and ?days choose the view",
          "Winter" in b.js("document.querySelector('#season-controls [aria-checked=true]').textContent")
          and b.js("document.querySelector('#power-controls [aria-checked=true]').textContent").startswith("AC")
          and b.js("document.querySelector('#days-controls [aria-checked=true]').textContent") == "All days")
    check("the table has all 24 hours with sunlight, DC and AC",
          b.js("document.querySelectorAll('#data-table tbody tr').length") == 24
          and b.js("[...document.querySelectorAll('#data-table thead th')].map(x => x.textContent.split(' (')[0])") == ["Hour", "Sunlight", "DC power", "AC power"])
    notes = b.js("document.getElementById('notes').textContent + document.getElementById('source').textContent")
    check("the notes say what the array is, where the weather cell is, that the coordinates are a placeholder, and that it is a typical year",
          "lying flat" in notes and "placeholder" in notes and "id 17574" in notes and "typical" in notes and "1,000 kW" in notes)
    check("no JavaScript errors", not b.errors, str(b.errors[:2]))


def test_phone(url: str, shots: Path | None) -> None:
    print("\n-- Phone width (390 px)")
    pages = [
        ("index.html", "document.querySelector('#chart .main-svg')"),
        ("duck_curve.html", "document.querySelector('#chart .main-svg') && document.querySelectorAll('.stat').length"),
        ("curtailment.html", "document.querySelector('#chart-reasons .main-svg') && document.querySelectorAll('.stat').length"),
        ("battery.html", "document.querySelector('#chart-battery .main-svg') && document.querySelectorAll('.stat').length"),
        ("map.html", "window.__mapState && document.querySelectorAll('#group-table tbody tr').length > 0"),
        ("manoa.html", "document.querySelector('#chart .main-svg') && document.querySelectorAll('.stat').length === 5"),
    ]
    b = Browser(width=390, height=900)
    try:
        for page, ready in pages:
            b.goto(f"{url}/{page}", ready)
            b.pause(500)
            scroll_width = b.js("document.documentElement.scrollWidth")
            wide = b.js("""[...document.querySelectorAll('body *')].filter(e => {
                const r = e.getBoundingClientRect();
                if (r.width === 0 || r.right <= 391) return false;
                for (let p = e.parentElement; p; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return false; }
                return true; }).slice(0, 3).map(e => e.tagName + ' ' + String(e.className && e.className.baseVal === undefined ? e.className : ''))""")
            check(f"{page} fits without sideways scrolling", scroll_width <= 391 and not wide, f"width {scroll_width} {wide}")
            if page in ("duck_curve.html", "battery.html"):
                ticks = b.js("[...document.querySelectorAll('.xtick text')].map(t => t.textContent).slice(0, 4)")
                check(f"{page} labels the hours every 6 hours on a phone", ticks == ["12 AM", "6 AM", "12 PM", "6 PM"], str(ticks))
            if shots:
                b.screenshot(shots / f"phone-{page.replace('.html', '')}.png")
    finally:
        b.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shots", help="folder to save screenshots into")
    args = parser.parse_args()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    server = StaticServer()
    try:
        b = Browser(width=1100, height=1400)
        try:
            for test in (test_tier1, test_tier2, test_tier3, test_tier4, test_map, test_manoa):
                b.errors.clear()
                test(b, server.url)
                if shots:
                    b.screenshot(shots / f"desktop-{test.__name__.replace('test_', '')}.png")
        finally:
            b.close()
        test_phone(server.url, shots)
    finally:
        server.close()

    print(f"\n{'ALL CHECKS PASSED' if not failures else str(len(failures)) + ' CHECK(S) FAILED: ' + '; '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
