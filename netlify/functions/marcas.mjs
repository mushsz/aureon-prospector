// Marcas compartilhadas das listas de leads (Enviado, Respondeu, visto no lote...), guardadas no Netlify Blobs.
//
//   GET  /api/marcas?lista=sites            -> { marcas: { "enviado:<id>": "1", "visto:<id>": "2026-10-23", ... } }
//   POST /api/marcas  { lista, ops: [...] }  -> { ok: true, n, ignoradas }
//        op: { op: "set" | "del", tipo, id, dia? }   (dia só no "visto")
//
// Uma chave por marca: <lista>/<tipo>/<id em base64url> (e <lista>/visto/<dia>/<id em base64url>). Quem lista as chaves já
// sabe tudo, sem ler valores. O id vai em base64url porque ele pode ter "/" (licitações: 15126437000305-1-004188/2026) e
// ":" (Maps), e o serviço de armazenamento pode ou não decodificar %2F: sem "%" nem "/" na chave, tanto faz.
// Sem login: a página já é noindex e o link só está com quem usa a lista.

const LISTAS = new Set(["sites", "hosp", "teste"]);   // "teste" é para o teste de fumaça depois do deploy
const TIPOS = new Set(["enviado", "respondeu", "participar", "proposta", "contato", "cadastro", "visto"]);
const ID = /^(?!\.+$)[\w:.\/-]{1,120}$/;   // 0x..:0x.. (Maps), 15126437000305-1-004188/2026 (PNCP), cnes-507237
const DIA = /^\d{4}-\d{2}-\d{2}$/;
const MAX_OPS = 300;
const EM_PARALELO = 20;
const DIAS_DE_FOLGA = 2;   // relógio de aparelho errado não pode queimar o lote de todo mundo

const codificar = id => Buffer.from(id, "utf8").toString("base64url");
const decodificar = s => Buffer.from(s, "base64url").toString("utf8");

const json = (corpo, status = 200) => new Response(JSON.stringify(corpo), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

const chave = (lista, op) => op.tipo === "visto"
  ? `${lista}/visto/${op.dia}/${codificar(op.id)}`
  : `${lista}/${op.tipo}/${codificar(op.id)}`;

function validar(op, agora) {
  if (!op || typeof op !== "object") return "operação inválida";
  if (op.op !== "set" && op.op !== "del") return "op deve ser set ou del";
  if (!TIPOS.has(op.tipo)) return "tipo desconhecido";
  if (typeof op.id !== "string" || !ID.test(op.id)) return "id inválido";
  if (op.tipo === "visto") {
    if (op.op !== "set" || !DIA.test(op.dia || "")) return "visto pede dia AAAA-MM-DD e só aceita set";
    const dias = Math.abs(Date.parse(op.dia + "T12:00:00Z") - agora) / 864e5;
    if (!(dias <= DIAS_DE_FOLGA + 0.5)) return "dia fora do que o relógio do servidor aceita";
  }
  return null;
}

export async function ler(lista, store) {
  const { blobs } = await store.list({ prefix: lista + "/" });
  const marcas = {};
  for (const { key } of blobs) {
    const [, tipo, a, b] = key.split("/");
    if (!TIPOS.has(tipo)) continue;
    if (tipo === "visto") {
      // se duas pessoas viram o mesmo lead em dias diferentes, vale o primeiro
      if (b === undefined) continue;
      const k = `visto:${decodificar(b)}`;
      if (!marcas[k] || a < marcas[k]) marcas[k] = a;
    } else if (a !== undefined) {
      marcas[`${tipo}:${decodificar(a)}`] = "1";
    }
  }
  return marcas;
}

export async function tratar(req, store, agora = Date.now()) {
  const url = new URL(req.url);
  if (req.method === "GET") {
    const lista = url.searchParams.get("lista");
    if (!LISTAS.has(lista)) return json({ erro: "lista desconhecida" }, 400);
    return json({ marcas: await ler(lista, store), agora: new Date(agora).toISOString() });
  }
  if (req.method === "POST") {
    let corpo;
    try { corpo = await req.json(); } catch { return json({ erro: "corpo não é JSON" }, 400); }
    if (!corpo || typeof corpo !== "object" || !LISTAS.has(corpo.lista)) return json({ erro: "lista desconhecida" }, 400);
    const ops = Array.isArray(corpo.ops) ? corpo.ops : [];
    if (!ops.length || ops.length > MAX_OPS) return json({ erro: `mande de 1 a ${MAX_OPS} operações` }, 400);
    // uma operação ruim não derruba as boas: ela é ignorada e contada
    const boas = ops.filter(op => !validar(op, agora));
    if (!boas.length) return json({ erro: validar(ops[0], agora) || "nenhuma operação válida" }, 400);
    const quando = new Date(agora).toISOString();
    for (let i = 0; i < boas.length; i += EM_PARALELO) {
      await Promise.all(boas.slice(i, i + EM_PARALELO).map(op => {
        const k = chave(corpo.lista, op);
        return op.op === "set" ? store.set(k, quando) : store.delete(k);
      }));
    }
    return json({ ok: true, n: boas.length, ignoradas: ops.length - boas.length });
  }
  return json({ erro: "método não aceito" }, 405);
}

export default async (req) => {
  const { getStore } = await import("@netlify/blobs");
  return tratar(req, getStore({ name: "marcas", consistency: "strong" }));
};

export const config = { path: "/api/marcas" };
