const COLUMNS = [
  { key: "overall_rank", label: "#", num: true },
  { key: "player", label: "Player" },
  { key: "pos", label: "Pos" },
  { key: "team", label: "Team" },
  { key: "score", label: "Score", num: true },
  { key: "auction_value", label: "Value", num: true },
  ...(DATA.has_market ? [
    { key: "market_price", label: "Market $", num: true },
    { key: "market_gap", label: "Value − market", num: true },
  ] : []),
  { key: "field_value", label: "Field $", num: true },
  { key: "scarcity_premium", label: "Scarcity", num: true },
  { key: "field_tier", label: "Field tier", num: true },
  { key: "tier_pos", label: "Pos" },
  { key: "tier", label: "Pos tier", num: true },
  { key: "scarcity_note", label: "Note" },
];
const ASCENDING_FIRST = new Set(["overall_rank", "player", "pos", "team", "field_tier", "tier_pos", "tier"]);
const state = { q: "", pos: "ALL", tierPos: "ALL", draftedOnly: false, sort: "overall_rank", dir: 1 };
const TIER_GROUPS = ["ALL", ...BASE];
const byPos = Object.fromEntries(TIER_GROUPS.map(p => [p, DATA.tiers.filter(t => t.pos === p).sort((a, b) => a.pos_rank - b.pos_rank)]));
const tierAt = new Map(DATA.tiers.map(t => [`${t.pos}|${t.player}`, t]));
const maxValue = Math.max(...DATA.players.map(p => p.auction_value));

function renderValuesStatic() {
  $("pos-body").innerHTML = DATA.positions.map(p => `
    <tr><td class="pos-tag">${p.pos}</td><td class="num">${p.eligible_drafted}</td><td class="num">${p.tiers}</td>
    <td class="num">${p.replacement.toFixed(2)}</td><td class="num">${p.vs_field.toFixed(2)}</td>
    <td class="cliffs">${(p.major_cliffs || "").split("; ").filter(Boolean).map(c => {
      const m = c.match(/^after (#\d+) (.+) \((-[\d.]+)\)$/);
      return m ? `<span class="chip">${m[1]} ${esc(m[2])} <b>${m[3]}</b></span>` : `<span class="chip">${esc(c)}</span>`;
    }).join("")}</td></tr>`).join("");

  $("tier-pos").innerHTML = segButtons(TIER_GROUPS, state.tierPos, "pos");
  $("posf").innerHTML = segButtons(TIER_GROUPS, state.pos, "pos");
  $("head").innerHTML = COLUMNS.map(c => `<th data-key="${c.key}" class="${c.num ? "num" : ""}" scope="col">${c.label}</th>`).join("");
}

function renderTiers() {
  const list = byPos[state.tierPos];
  const groups = [];
  list.forEach(t => { (groups[t.pos_tier - 1] ||= []).push(t); });
  $("tiers").innerHTML = groups.filter(Boolean).map((g, i, all) => {
    const drafted = g.filter(t => t.drafted).map(t => t.auction_value);
    const range = drafted.length ? (drafted.length > 1 ? `${money(Math.max(...drafted))}–${money(Math.min(...drafted))}` : money(drafted[0])) : "not drafted";
    const last = g[g.length - 1];
    const chips = g.map(t => `<a class="tp ${t.drafted ? "" : "undrafted"}" href="${esc(t.source_url)}" target="_blank" rel="noopener" title="${esc(t.team || "FA")} · score ${t.score.toFixed(2)}"><span class="r">${t.pos_rank}</span><span class="n">${esc(t.player)}</span><span class="v">${t.drafted ? money(t.auction_value) : "–"}</span></a>`).join("");
    const brk = i < all.length - 1 && last.cliff_strength
      ? `<div class="brk ${last.cliff_strength}">${last.cliff_strength === "major" ? "Major cliff" : "Tier break"} −${last.gap_to_next.toFixed(2)}</div>` : "";
    return `<div class="tier"><div class="tier-label"><strong>Tier ${g[0].pos_tier}</strong><span>${g.length} player${g.length > 1 ? "s" : ""} · ${range}</span></div><div class="tier-players">${chips}</div></div>${brk}`;
  }).join("");
  const who = state.tierPos === "ALL" ? "players" : `${state.tierPos}-eligible players`;
  $("tier-count").textContent = `${groups.filter(Boolean).length} tiers · ${list.length} ${who}`;
  document.querySelectorAll("#tier-pos button").forEach(b => b.setAttribute("aria-pressed", b.dataset.pos === state.tierPos));
}

// Pos tier: at the filtered position, or the player's scarce position under ALL.
// Break lines follow the active filter: field tiers under ALL, that position's tiers otherwise.
function withTier(p) {
  const pos = state.pos === "ALL" ? p.scarce_pos : state.pos;
  const t = tierAt.get(`${pos}|${p.player}`);
  const line = tierAt.get(`${state.pos}|${p.player}`);
  return { ...p, tier_pos: pos, tier: t ? t.pos_tier : null, cut: line ? line.cliff_strength : "" };
}

function rows() {
  const q = state.q.toLowerCase();
  return DATA.players
    .filter(p => !state.draftedOnly || p.drafted)
    .filter(p => state.pos === "ALL" || p.pos.split("/").includes(state.pos))
    .filter(p => !q || p.player.toLowerCase().includes(q) || (p.team || "").toLowerCase().includes(q))
    .map(withTier)
    .sort((a, b) => {
      const x = a[state.sort], y = b[state.sort];
      if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * state.dir;
    });
}

function cell(p, c) {
  const v = p[c.key];
  switch (c.key) {
    case "player": return `<td class="player"><a href="${esc(p.source_url)}" target="_blank" rel="noopener">${esc(v)}</a></td>`;
    case "pos": return `<td class="pos-cell">${esc(v)}</td>`;
    case "team": return `<td class="team">${esc(v || "FA")}</td>`;
    case "score": return `<td class="num">${v.toFixed(2)}</td>`;
    case "auction_value": return `<td class="num"><div class="money"><div class="track"><div class="bar" style="width:${(v / maxValue * 100).toFixed(1)}%"></div></div><b>${money(v)}</b></div></td>`;
    case "field_value": return `<td class="num">${money(v)}</td>`;
    case "market_price":
      if (v == null) return `<td class="num">–</td>`;
      return p.market_listed === false ? `<td class="num" title="Not in the market list: goes for under $${DATA.league.min_bid}">${money(v)}*</td>` : `<td class="num">${money(v)}</td>`;
    case "market_gap":
      if (v == null || Math.abs(v) < 0.5) return `<td class="num">–</td>`;
      return `<td class="num ${v > 0 ? "up" : "down"}" title="${v > 0 ? "Market pays less than our value" : "Market pays more than our value"}">${v > 0 ? "+" : "−"}$${Math.abs(v).toFixed(0)}</td>`;
    case "scarcity_premium":
      if (!p.drafted || Math.abs(v) < 0.05) return `<td class="num">–</td>`;
      return `<td class="num ${v > 0 ? "up" : "down"}">${v > 0 ? "+" : "−"}$${Math.abs(v).toFixed(1)}</td>`;
    case "scarcity_note": return `<td class="note">${esc(v)}</td>`;
    default: return `<td class="${c.num ? "num" : ""}">${esc(v)}</td>`;
  }
}

function renderTable() {
  const list = rows();
  // Break lines only make sense in rank order.
  const inOrder = state.sort === "overall_rank" && state.dir === 1;
  $("body").innerHTML = list.map(p => {
    const cls = [p.drafted ? "" : "undrafted", inOrder && p.cut ? `cut-${p.cut}` : ""].join(" ").trim();
    return `<tr class="${cls}">${COLUMNS.map(c => cell(p, c)).join("")}</tr>`;
  }).join("");
  $("count").textContent = `${list.length} players`;
  document.querySelectorAll("#head th").forEach(th => {
    th.setAttribute("aria-sort", th.dataset.key === state.sort ? (state.dir === 1 ? "ascending" : "descending") : "none");
  });
  document.querySelectorAll("#posf button").forEach(b => b.setAttribute("aria-pressed", b.dataset.pos === state.pos));
}

$("tier-pos").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { state.tierPos = b.dataset.pos; renderTiers(); } });
$("q").addEventListener("input", e => { state.q = e.target.value; renderTable(); });
$("drafted-only").addEventListener("change", e => { state.draftedOnly = e.target.checked; renderTable(); });
$("posf").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { state.pos = b.dataset.pos; renderTable(); } });
$("head").addEventListener("click", e => {
  const th = e.target.closest("th"); if (!th) return;
  const key = th.dataset.key;
  if (state.sort === key) state.dir *= -1;
  else { state.sort = key; state.dir = ASCENDING_FIRST.has(key) ? 1 : -1; }
  renderTable();
});
renderValuesStatic();
renderTiers();
renderTable();
