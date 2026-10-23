(async () => {
  const S = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 30 && !document.querySelector('[role="main"] h1'); i++) await S(400);
  await S(900);
  const corpo = document.body ? document.body.innerText : "";
  if (/tráfego incomum|unusual traffic|não sou um robô|recaptcha/i.test(corpo) || location.pathname.startsWith("/sorry")) return { bloqueio: true };
  const m = document.querySelector('[role="main"]');
  if (!m) return {};
  const t = m.innerText;
  const rotulo = sel => (m.querySelector(sel)?.getAttribute("aria-label") || "").trim();
  const nota = (t.match(/\n(\d,\d)\n\(([\d.]+)\)/) || []);
  const horas = [...m.querySelectorAll('[aria-label*="Copiar horário"]')].map(e => e.getAttribute("aria-label").replace(/, Copiar horário de funcionamento/, ""));
  const reviews = [...m.querySelectorAll("[data-review-id]")].filter(e => !e.parentElement.closest("[data-review-id]")).map(e => {
    const x = e.innerText;
    const quando = (x.match(/(\d+|um|uma) (dias?|semanas?|m[eê]s|meses|anos?) atrás|há (\d+|um|uma) (dias?|semanas?|m[eê]s|meses|anos?)/) || [""])[0];
    const est = (e.querySelector('[role="img"][aria-label*="estrela"]')?.getAttribute("aria-label") || "").match(/\d/);
    return { quando, estrelas: est ? +est[0] : null, resposta: /Resposta do proprietário/.test(x) };
  });
  return {
    nome: m.querySelector("h1")?.innerText.trim() || "",
    nota: nota[1] ? +nota[1].replace(",", ".") : null,
    avaliacoes: nota[2] ? +nota[2].replace(/\./g, "") : 0,
    categoria: m.querySelector('button[jsaction*="category"]')?.innerText.trim() || "",
    endereco: rotulo('[data-item-id="address"]').replace(/^Endereço:\s*/, ""),
    telefone: rotulo('[data-item-id^="phone"]').replace(/^Telefone:\s*/, ""),
    site: m.querySelector('a[data-item-id="authority"]')?.href || "",
    horario: [...new Set(horas.map(h => h.split(", ").slice(1).join(", ")))].length === 1 ? horas[0]?.split(", ").slice(1).join(", ") : horas.join(" | "),
    atributos: [...m.querySelectorAll('[data-item-id^="place-info-links"]')].map(e => e.innerText.trim()).filter(Boolean),
    fotos: +((t.match(/([\d.]+) fotos?/) || [0, 0])[1] + "").replace(/\./g, ""),
    destaques: [...t.matchAll(/\n\s*"([^"\n]{12,200})"/g)].map(x => x[1]).slice(0, 3),
    topicos: [...m.querySelectorAll('button[aria-label*="mencionado em"]')].map(e => e.getAttribute("aria-label").match(/^(.+), mencionado em (\d+)/)).filter(Boolean).map(x => [x[1], +x[2]]),
    reviews: reviews.slice(0, 5),
    descricao: (t.match(/\nDa empresa\n([^\n]{10,400})/) || [])[1] || "",
    fechado: /Fechado permanentemente|Fechado temporariamente/.test(t),
    reivindicada: !/Reivindicar esta empresa|Você é o proprietário/i.test(t),
  };
})()
