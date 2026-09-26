const BASE = ["PG", "SG", "SF", "PF", "C"];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = v => "$" + v.toFixed(v >= 10 ? 0 : 1);
const $ = id => document.getElementById(id);
const segButtons = (list, active, attr) => list.map(p => `<button type="button" data-${attr}="${p}" aria-pressed="${p === active}">${p}</button>`).join("");
const sum = (list, key) => list.reduce((s, x) => s + x[key], 0);

// Star labels (one definition, from our tiers; see scarcity.star_tiers and market.star_labels).
const STAR_LABEL = new Map(DATA.players.filter(p => p.star_label).map(p => [p.player, p.star_label]));
const STAR_BADGE = {
  "Star": ["star", "★ Star"],
  "Underpriced star": ["star-under", "★ Underpriced"],
  "Overpriced star": ["star-over", "★ Overpriced"],
  "Hype": ["hype", "Hype"],
};
const starBadge = name => {
  const label = STAR_LABEL.get(name);
  if (!label) return "";
  const [cls, text] = STAR_BADGE[label];
  return ` <span class="badge ${cls}" title="${label}">${text}</span>`;
};

(function renderHeader() {
  const L = DATA.league;
  const drafted = DATA.players.filter(p => p.drafted);
  const total = sum(drafted, "auction_value");
  const roster = Object.entries(L.roster).map(([slot, n]) => slot === "BN" ? `+ ${n} BN` : Array(n).fill(slot).join(" ")).join(" ");
  $("eyebrow").textContent = DATA.title;
  $("settings").innerHTML = [
    ["League", `${L.teams} teams · 9-cat roto`], ["Budget", `$${L.budget} each · $${(L.budget * L.teams).toLocaleString()} total`],
    ["Roster", roster], ["Games cap", L.games_cap ? `${L.games_cap} (~${L.core_size} players count)` : "none"],
    ["Drafted", `${drafted.length} of ${DATA.players.length}`], ["Values sum", "$" + Math.round(total).toLocaleString()],
  ].map(([k, v]) => `<span>${k} <b>${esc(v)}</b></span>`).join("");
})();

// Page tabs: #strategy opens the strategy view; anything else shows values.
let currentView = location.hash === "#strategy" ? "strategy" : "values";
function showView() {
  document.querySelectorAll(".view").forEach(v => { v.hidden = v.id !== `view-${currentView}`; });
  document.querySelectorAll(".tabs a").forEach(a => a.setAttribute("aria-currentView", a.dataset.view === currentView ? "page" : "false"));
}
document.querySelector(".tabs").addEventListener("click", e => {
  const a = e.target.closest("a"); if (!a) return;
  e.preventDefault();
  try { history.replaceState(null, "", a.dataset.view === "strategy" ? "#strategy" : "#values"); } catch (_) { /* sandboxed */ }
  currentView = a.dataset.view;
  showView();
  window.scrollTo(0, 0);
});
showView();
