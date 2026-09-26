const S = DATA.strategy;
const L = DATA.league;
const playerBy = new Map(DATA.players.map(p => [p.player, p]));
const planner = { picks: [], plan: S.plans[0].key };
const slotLabel = s => (s === S.bench ? "Bench" : s);

function canFill(slot, pos) {
  const allowed = DATA.slot_eligibility[slot];
  return !allowed || pos.split("/").some(t => allowed.includes(t));
}

// Bipartite matching (Kuhn) over the starting slots, best score first. A matched player
// is only ever moved to another slot, never dropped, so the best players start; everyone
// else fills the bench in score order (core bench spots first).
function assignSlots(picks) {
  const slots = S.roster_slots;
  const owner = Array(slots.length).fill(-1);
  const order = picks.map((_, i) => i).sort((a, b) => picks[b].score - picks[a].score);
  const place = (i, usable) => {
    const tryAssign = (k, seen) => {
      for (let j = 0; j < slots.length; j++) {
        if (!usable(j) || seen[j] || !canFill(slots[j], picks[k].pos)) continue;
        seen[j] = true;
        if (owner[j] === -1 || tryAssign(owner[j], seen)) { owner[j] = k; return true; }
      }
      return false;
    };
    return tryAssign(i, Array(slots.length).fill(false));
  };
  const starting = j => slots[j] !== S.bench;
  const benchSlots = slots.map((_, j) => j).filter(j => !starting(j));
  const unplaced = [];
  order.filter(i => !place(i, starting)).forEach(i => {
    const j = benchSlots.find(k => owner[k] === -1);
    if (j === undefined) unplaced.push(picks[i]); else owner[j] = i;
  });
  return { owner, unplaced };
}

// What you'd expect to pay: the market price when loaded, otherwise our value.
const expectedPrice = p => Math.max(L.min_bid, Math.round(p.market_price ?? p.auction_value));

function pickFor(name, price) {
  const p = playerBy.get(name);
  return { player: name, pos: p.pos, score: p.score, field_tier: p.field_tier, price };
}

function loadPlan(key) {
  const plan = S.plans.find(p => p.key === key);
  planner.plan = key;
  planner.picks = [...plan.picks, ...plan.bench].map(p => pickFor(p.player, p.price));
  renderPlanner();
}

function budgetState() {
  const picks = planner.picks;
  const spent = sum(picks, "price");
  const left = L.budget - spent;
  const spotsLeft = S.roster_slots.length - picks.length;
  const coreLeft = Math.max(0, S.core_size - picks.length);
  const longLeft = spotsLeft - coreLeft;
  const coreScore = picks.map(p => p.score).sort((a, b) => b - a).slice(0, S.core_size).reduce((s, x) => s + x, 0);
  // Hold back the long-shot money for the bench spots still open.
  const benchReserve = S.bench_spots ? S.bench_budget * (longLeft / S.bench_spots) : 0;
  const maxBid = spotsLeft > 0 ? left - L.min_bid * (spotsLeft - 1) : 0;
  const perCore = coreLeft > 0 ? (left - benchReserve) / coreLeft : null;
  const coreMax = coreLeft > 0 ? left - benchReserve - L.min_bid * (coreLeft - 1) : null;
  return { spent, left, spotsLeft, coreLeft, coreScore, maxBid, perCore, coreMax };
}

// Unpicked core players priced near a target, to show what an average spot buys.
function buysAround(target) {
  const taken = new Set(planner.picks.map(p => p.player));
  return DATA.players
    .filter(p => p.core && !taken.has(p.player))
    .sort((a, b) => Math.abs(a.auction_value - target) - Math.abs(b.auction_value - target))
    .slice(0, 3);
}

function renderPlanner() {
  const { owner, unplaced } = assignSlots(planner.picks);
  $("roster-body").innerHTML = S.roster_slots.map((slot, j) => {
    const p = owner[j] === -1 ? null : planner.picks[owner[j]];
    const longShot = j >= S.core_size;
    const divider = j === S.core_size ? `<tr class="divider"><td colspan="6">Long shots · about $${S.bench_budget} total · their games mostly won't count</td></tr>` : "";
    const cells = p
      ? `<td class="player">${esc(p.player)} <span class="pos-cell">${esc(p.pos)}</span></td>
         <td class="num"><input class="price" type="number" min="1" step="1" value="${p.price}" data-player="${esc(p.player)}" aria-label="Price for ${esc(p.player)}"></td>
         <td class="num">${p.score.toFixed(2)}</td><td class="num">${p.field_tier}</td>
         <td><button type="button" class="remove" data-player="${esc(p.player)}" aria-label="Remove ${esc(p.player)}">Remove</button></td>`
      : `<td class="empty">Empty</td><td></td><td></td><td></td><td></td>`;
    return `${divider}<tr class="${longShot ? "long" : ""}"><td class="slot">${slotLabel(slot)}</td>${cells}</tr>`;
  }).join("");

  const b = budgetState();
  $("budget-stats").innerHTML = [
    ["Spent", `$${b.spent}`, ""], ["Left", `$${b.left}`, b.left < 0 ? "bad" : ""],
    ["Open spots", `${b.spotsLeft} (${b.coreLeft} core)`, ""],
    ["Max bid now", b.spotsLeft ? `$${Math.max(0, b.maxBid)}` : "–", ""],
    ["Max core bid (keeps long-shot money)", b.coreMax == null ? "–" : `$${Math.max(0, Math.floor(b.coreMax))}`, ""],
    ["Avg per open core spot", b.perCore == null ? "–" : money(b.perCore), ""],
    ["Core score", b.coreScore.toFixed(2), ""],
  ].map(([k, v, cls]) => `<div><dt>${k}</dt><dd class="${cls}">${v}</dd></div>`).join("");

  const notes = [];
  if (unplaced.length) notes.push(`No open slot fits ${unplaced.map(p => p.player).join(", ")}. Check positions.`);
  if (b.left < 0) notes.push(`Over budget by $${-b.left}.`);
  if (b.perCore != null && b.perCore > 0) {
    const near = buysAround(b.perCore);
    notes.push(`${money(b.perCore)} per open core spot buys players like ${near.map(p => `${p.player} (${money(p.auction_value)})`).join(", ")}.`);
  }
  $("budget-advice").textContent = notes.join(" ");

  const taken = new Set(planner.picks.map(p => p.player));
  $("player-list").innerHTML = DATA.players.filter(p => !taken.has(p.player)).map(p => `<option value="${esc(p.player)}">${esc(p.pos)} · ${money(p.auction_value)}</option>`).join("");
  document.querySelectorAll("#plan-pick button").forEach(x => x.setAttribute("aria-pressed", x.dataset.plan === planner.plan));
}

function renderStrategyStatic() {
  const pay = S.tier_guide.filter(t => t.advice === "Pay up");
  const deep = S.tier_guide.filter(t => t.advice.startsWith("Deep")).sort((a, b) => b.count - a.count)[0];
  const elite = pay.slice(0, 3).map(t => t.players).join(", ");
  const bigDrop = pay.reduce((m, t) => Math.max(m, t.drop_after || 0), 0);
  const caps = Object.entries(L.core_position_caps).map(([p, n]) => `${n} ${p}-eligible`).join(", ");
  $("core-n").textContent = S.core_size;

  $("facts").innerHTML = [
    [S.core_size, "players count", `${S.games_cap} games ÷ ${S.games_per_player}`],
    [`$${S.core_budget}`, "core budget", `$${L.budget} minus $${S.bench_budget} for long shots`],
    [money(S.core_budget / S.core_size), "avg per core spot", "if spread evenly"],
    [S.bench_spots, "long shots", `$${S.bench_budget} total, $${L.min_bid}–3 each`],
  ].map(([v, k, sub]) => `<div class="fact"><strong>${v}</strong><span>${k}</span><small>${sub}</small></div>`).join("");

  $("rules").innerHTML = [
    `<b>Spend on the ${S.core_size} whose games count.</b> ${S.games_cap} games is about ${S.core_size} full seasons, so the other ${S.bench_spots} roster spots mostly sit. Put $${S.core_budget} into the core and keep about $${S.bench_budget} for the ${S.bench_spots} long shots.`,
    `<b>Pay up before a major cliff.</b> ${esc(elite)} have no replacement once they're gone; the biggest drop after them is ${bigDrop.toFixed(2)} points. Tiers marked Pay up below are the ones you can't make up later.`,
    deep ? `<b>Be patient in deep tiers.</b> Tier ${deep.tier} has ${deep.count} near-equal players at $${Math.round(deep.low)}–$${Math.round(deep.high)}. If one goes over value, let it go; the next one is the same player on paper.` : "",
    `<b>Know your max bid.</b> The hard limit is money left minus $${L.min_bid} for every other open spot. For core buys, also hold back what's left of the $${S.bench_budget} long-shot money. The planner tracks both.`,
    S.price_bands ? `<b>Hunt steals in the $10–20 range.</b> That's where seasons are made: players the market prices there but we value far higher. Any core-quality player under $10 is a big win, for a starting slot or the bench. See Where the steals are below.` : "",
    S.price_bands ? `<b>Expect to overpay for stars.</b> Anyone the market prices at $${S.star_price}+ usually goes ${Math.round(S.star_premium * 100)}% over average. Pay it for a player right before a major cliff; let hype-only stars go.` : "",
    `<b>Balance categories yourself.</b> The score is one number, so it can't see category fit. The sample plans allow at most ${caps} core players so they don't stack big men.`,
  ].filter(Boolean).map(r => `<li>${r}</li>`).join("");

  $("plan-pick").innerHTML = S.plans.map(p => `<button type="button" data-plan="${p.key}" aria-pressed="false">${esc(p.name)}</button>`).join("");

  const atValue = S.plans.filter(p => p.cost === "value").map(p => p.total_score);
  const market = S.plans.find(p => p.cost === "expected") || S.plans.find(p => p.cost === "market");
  $("plans-lede").textContent = `At our values, every plan spends $${S.core_budget} on the core and projects a core score between ${Math.min(...atValue).toFixed(1)} and ${Math.max(...atValue).toFixed(1)}. Prices are fair, so no build pulls far ahead; the max-score plan is the ceiling. ` +
    (market ? `At realistic market prices (stars at a premium) the best core scores ${market.total_score.toFixed(1)}: players the market underrates let $${market.spend} buy $${market.worth} of our value. That's the plan to draft from.` : `The real edge is paying under value. Load market prices to find where.`);
  $("plan-grid").innerHTML = S.plans.map(p => `
    <article class="plan">
      <header><h3>${esc(p.name)}</h3><p>${esc(p.summary)}</p></header>
      <div class="plan-totals"><span>Spend <b>$${p.spend}</b>${p.cost === "market" ? " at market" : ""}</span><span>Core score <b>${p.total_score.toFixed(2)}</b></span>${p.worth !== p.spend ? `<span>Worth <b>$${p.worth}</b> at our value</span>` : ""}</div>
      <ul>${p.picks.map(x => `<li><span class="slot">${slotLabel(x.slot)}</span><span class="n">${esc(x.player)}</span><span class="v">$${x.price}</span></li>`).join("")}</ul>
      <p class="bench-label">Long shots · $${p.bench_spend}</p>
      <ul class="bench-list">${p.bench.map(x => `<li><span class="slot">Bench</span><span class="n">${esc(x.player)}</span><span class="v">$${x.price}</span></li>`).join("")}</ul>
      <button type="button" class="ghost" data-load="${p.key}">Open in planner</button>
    </article>`).join("");

  const adviceClass = a => (a === "Pay up" ? "pay" : a.startsWith("Deep") ? "wait" : "fair");
  $("guide-body").innerHTML = S.tier_guide.map(t => `
    <tr><td class="num">${t.tier}</td><td><span class="pill ${adviceClass(t.advice)}">${t.advice}</span></td>
    <td class="num">${t.count > 1 ? `$${Math.round(t.high)}–$${Math.round(t.low)}` : `$${Math.round(t.high)}`}</td>
    <td class="num">${t.count}</td><td class="num">${t.drop_after == null ? "–" : `−${t.drop_after.toFixed(2)}${t.cliff === "major" ? " major" : ""}`}</td>
    <td class="who">${esc(t.players)}</td></tr>`).join("");

  renderPositionCards();
  renderMarket();
  $("long-shots-plan").textContent = S.long_shots_plan;
  $("long-list").innerHTML = S.long_shots.map(p => `<span class="tp"><span class="n">${esc(p.player)}</span><span class="r">${esc(p.pos)} · ${esc(p.team || "FA")} · worth ${money(p.value_price)}</span><span class="v">$${p.price}</span></span>`).join("");
}

function renderMarket() {
  $("market-gaps").hidden = !S.price_bands;
  $("market-missing").hidden = !!S.price_bands;
  if (!S.price_bands) return;
  const premium = Math.round(S.star_premium * 100);
  $("bands-lede").textContent = `Our tiers say who is good. The market says what people pay, hype and scarcity included. Expect to pay ${premium}% over the average for anyone the market prices at $${S.star_price}+, and pay it when a cliff follows. Then hunt the $10–20 range, where seasons are made, and treat any core-quality player under $10 as a big win, for starters and bench alike. * means not in the market list, so he usually goes for the minimum. Market prices are ESPN-wide averages, not your league's.`;
  const listed = p => `${money(p.market_price)}${p.market_listed === false ? "*" : ""}`;
  const gap = v => `<td class="num ${v > 0 ? "up" : "down"}">${v > 0 ? "+" : "−"}$${Math.abs(v).toFixed(0)}</td>`;
  const role = p => (p.core ? `Starter · tier ${p.field_tier}` : "Bench");
  const starRow = p => {
    const over = p.expected_price - p.auction_value;
    const verdict = over > 5 ? `<span class="pill fair">Overpriced by $${Math.round(over)}</span>` : over < -5 ? `<span class="pill wait">Still a buy</span>` : `<span class="pill pay">Fair: pay it</span>`;
    return `<tr><td>${esc(p.player)} <span class="pos-cell">${esc(p.pos)}</span></td><td class="num">${money(p.auction_value)}</td><td class="num">${listed(p)}</td><td class="num"><b>${money(p.expected_price)}</b></td><td>${verdict}</td></tr>`;
  };
  const stealRow = p => `<tr><td>${esc(p.player)} <span class="pos-cell">${esc(p.pos)}</span></td><td class="pos-cell">${role(p)}</td><td class="num">${money(p.auction_value)}</td><td class="num">${listed(p)}</td>${gap(p.market_gap)}</tr>`;
  const card = (title, sub, head, rows, wide = false) => `<article class="band${wide ? " wide" : ""}"><header><h3>${title}</h3><p>${sub}</p></header><div class="scroll"><table><thead><tr>${head.map(h => `<th class="${h.num ? "num" : ""}">${h.label}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div></article>`;
  const H = (label, num = false) => ({ label, num });
  $("bands").innerHTML = S.price_bands.map(b => b.kind === "stars"
    ? card(b.label, `What top talent will really cost: market + ${premium}% when the market treats him as a star.`, [H("Player"), H("Value", 1), H("Market", 1), H("Plan to pay", 1), H("")], b.players.map(starRow).join(""), true)
    : card(b.label, b.high ? `Market price $${b.low}–$${b.high}, biggest gap to our value first.` : `Market price under $${b.high ?? b.low}`, [H("Player"), H("Role"), H("Value", 1), H("Market", 1), H("Gap", 1)], b.players.map(stealRow).join(""))
  ).join("") + (S.market ? card("Overpriced: let them go", "The market pays well over our value. Let someone else spend on hype.", [H("Player"), H("Role"), H("Value", 1), H("Market", 1), H("Gap", 1)], S.market.overpriced.slice(0, 10).map(stealRow).join("")) : "");
}

function renderPositionCards() {
  const where = Object.entries(DATA.slot_eligibility)
    .filter(([slot]) => S.roster_slots.includes(slot) && slot !== S.bench)
    .map(([slot, allowed]) => allowed && !(allowed.length === 1 && allowed[0] === slot) ? `${slot} (${allowed.filter(a => BASE.includes(a)).join(" or ")})` : allowed ? slot : `${slot} (anyone)`);
  $("slot-note").textContent = `Your lineup: ${where.join(", ")}, plus ${S.roster_slots.filter(s => s === S.bench).length} bench. Multi-position players fill whichever slot is open, so eligibility at a thin position is worth a little extra.`;

  $("pos-cards").innerHTML = BASE.map(pos => {
    const list = DATA.tiers.filter(t => t.pos === pos && t.core).sort((a, b) => a.pos_rank - b.pos_rank);
    const top = list.filter(t => t.pos_tier === 1).map(t => t.player);
    const withGap = list.slice(0, -1);
    const drop = withGap.reduce((m, t) => (t.gap_to_next > (m?.gap_to_next ?? -1) ? t : m), null);
    const after = drop ? list[list.indexOf(drop) + 1] : null;
    const tiers = new Set(list.map(t => t.pos_tier)).size;
    return `<article class="pos-card">
      <h3>${pos}</h3>
      <dl>
        <div><dt>Core-pool players</dt><dd>${list.length} in ${tiers} tiers</dd></div>
        <div><dt>Top tier</dt><dd>${esc(top.join(", "))}</dd></div>
        ${drop ? `<div><dt>Biggest drop</dt><dd>−${drop.gap_to_next.toFixed(2)} after ${esc(drop.player)}</dd></div>
        <div><dt>Best value after it</dt><dd>${esc(after.player)} at ${money(after.auction_value)}</dd></div>` : ""}
      </dl>
    </article>`;
  }).join("");
}

$("plan-pick").addEventListener("click", e => { const b = e.target.closest("button"); if (b) loadPlan(b.dataset.plan); });
$("plan-grid").addEventListener("click", e => {
  const b = e.target.closest("[data-load]"); if (!b) return;
  loadPlan(b.dataset.load);
  $("planner").scrollIntoView({ behavior: "smooth" });
});
$("clear-plan").addEventListener("click", () => { planner.picks = []; planner.plan = null; renderPlanner(); });
$("roster-body").addEventListener("click", e => {
  const b = e.target.closest(".remove"); if (!b) return;
  planner.picks = planner.picks.filter(p => p.player !== b.dataset.player);
  renderPlanner();
});
$("roster-body").addEventListener("change", e => {
  const input = e.target.closest(".price"); if (!input) return;
  const pick = planner.picks.find(p => p.player === input.dataset.player);
  pick.price = Math.max(L.min_bid, Math.round(Number(input.value) || L.min_bid));
  renderPlanner();
});
$("add-player").addEventListener("input", e => {
  const p = playerBy.get(e.target.value);
  if (p) $("add-price").value = expectedPrice(p);
});
$("add-btn").addEventListener("click", () => {
  const name = $("add-player").value;
  const hint = $("add-hint");
  if (!playerBy.has(name)) { hint.textContent = "Pick a name from the list."; return; }
  if (planner.picks.some(p => p.player === name)) { hint.textContent = `${name} is already on the roster.`; return; }
  if (planner.picks.length >= S.roster_slots.length) { hint.textContent = "Roster is full. Remove someone first."; return; }
  const price = Math.max(L.min_bid, Math.round(Number($("add-price").value) || L.min_bid));
  planner.picks.push(pickFor(name, price));
  $("add-player").value = ""; $("add-price").value = ""; hint.textContent = "";
  renderPlanner();
});

renderStrategyStatic();
loadPlan(planner.plan);
