// node teste_marcas.mjs   (usa um armazenamento falso na memória, sem rede)
import assert from "node:assert/strict";
import { tratar, ler } from "./netlify/functions/marcas.mjs";

// dois armazenamentos: um guarda a chave como veio; o outro age como um servidor que decodifica %xx uma vez
const falso = (decodifica = false) => {
  const m = new Map(), d = k => decodifica ? decodeURIComponent(k) : k;
  return {
    m, atrasoMs: 0, concorrentes: 0, maximo: 0,
    async set(k, v) { this.concorrentes++; this.maximo = Math.max(this.maximo, this.concorrentes); await new Promise(r => setTimeout(r, this.atrasoMs)); m.set(d(k), v); this.concorrentes--; },
    async delete(k) { m.delete(d(k)); },
    async list({ prefix }) { return { blobs: [...m.keys()].filter(k => k.startsWith(prefix)).sort().map(key => ({ key })) }; },
  };
};
const post = corpo => new Request("http://x/api/marcas", { method: "POST", body: typeof corpo === "string" ? corpo : JSON.stringify(corpo) });
const get = q => new Request("http://x/api/marcas?" + q);
const corpo = async r => ({ status: r.status, ...(await r.json()) });
const hoje = new Date().toISOString().slice(0, 10);
const diaDe = n => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);

for (const decodifica of [false, true]) {
  const s = falso(decodifica), nome = decodifica ? "(servidor que decodifica)" : "(chave como veio)";
  let r = await corpo(await tratar(post({ lista: "sites", ops: [
    { op: "set", tipo: "enviado", id: "0x95199db1d423e695:0x644d5d4a993358a9" },
    { op: "set", tipo: "proposta", id: "15126437000305-1-004188/2026" },
    { op: "set", tipo: "visto", id: "15126437000305-1-004188/2026", dia: hoje },
    { op: "set", tipo: "visto", id: "cnes-507237", dia: hoje },
  ] }), s));
  assert.equal(r.status, 200, nome); assert.equal(r.n, 4); assert.equal(r.ignoradas, 0);
  r = await corpo(await tratar(get("lista=sites"), s));
  assert.deepEqual(r.marcas, {
    "enviado:0x95199db1d423e695:0x644d5d4a993358a9": "1",
    "proposta:15126437000305-1-004188/2026": "1",
    ["visto:15126437000305-1-004188/2026"]: hoje,
    "visto:cnes-507237": hoje,
  }, nome);
  assert.deepEqual((await corpo(await tratar(get("lista=hosp"), s))).marcas, {}, "listas não se misturam");
  await tratar(post({ lista: "sites", ops: [{ op: "del", tipo: "enviado", id: "0x95199db1d423e695:0x644d5d4a993358a9" }] }), s);
  assert.equal(Object.keys(await ler("sites", s)).length, 3);
  // visto duas vezes em dias diferentes: vale o primeiro
  await tratar(post({ lista: "sites", ops: [{ op: "set", tipo: "visto", id: "cnes-507237", dia: diaDe(1) }] }), s);
  assert.equal((await ler("sites", s))["visto:cnes-507237"], hoje);
  // marcar duas vezes não duplica
  await tratar(post({ lista: "sites", ops: [{ op: "set", tipo: "proposta", id: "15126437000305-1-004188/2026" }] }), s);
  assert.equal(Object.keys(await ler("sites", s)).length, 3);
}

// uma operação ruim não derruba as boas
{
  const s = falso();
  const r = await corpo(await tratar(post({ lista: "sites", ops: [
    { op: "set", tipo: "enviado", id: "a1" }, { op: "set", tipo: "enviado", id: "id com espaço" }, { op: "set", tipo: "tipo-novo", id: "a2" },
    { op: "set", tipo: "enviado", id: ".." }, { op: "set", tipo: "enviado", id: "a3" }] }), s));
  assert.deepEqual([r.status, r.n, r.ignoradas], [200, 2, 3]);
  assert.deepEqual(Object.keys(await ler("sites", s)).sort(), ["enviado:a1", "enviado:a3"]);
}
// só ruins, ou formato errado: 400
const s = falso();
for (const ruim of [
  { lista: "outra", ops: [{ op: "set", tipo: "enviado", id: "a" }] },
  { lista: "sites", ops: [{ op: "set", tipo: "enviado", id: "../x y" }] },
  { lista: "sites", ops: [{ op: "set", tipo: "visto", id: "a" }] },
  { lista: "sites", ops: [{ op: "del", tipo: "visto", id: "a", dia: hoje }] },
  { lista: "sites", ops: [{ op: "set", tipo: "visto", id: "a", dia: "2020-01-01" }] },   // relógio errado
  { lista: "sites", ops: [{ op: "set", tipo: "visto", id: "a", dia: diaDe(20) }] },
  { lista: "sites", ops: [] },
  { lista: "sites", ops: Array(301).fill({ op: "set", tipo: "enviado", id: "a" }) },
  null, [], "nada", 5,
]) assert.equal((await tratar(post(ruim === null ? "null" : ruim), s)).status, 400, JSON.stringify(ruim)?.slice(0, 80));
assert.equal((await tratar(get("lista=nada"), s)).status, 400);
assert.equal((await tratar(new Request("http://x/api/marcas", { method: "PUT" }), s)).status, 405);
assert.equal((await tratar(post("isso nao e json"), s)).status, 400);
// dia de ontem e de amanhã valem (fuso do Brasil x relógio do servidor)
assert.equal((await tratar(post({ lista: "teste", ops: [{ op: "set", tipo: "visto", id: "a", dia: diaDe(-1) }, { op: "set", tipo: "visto", id: "b", dia: diaDe(1) }] }), s)).status, 200);

// lote grande: grava em grupos de 20, não um por um
{
  const g = falso(); g.atrasoMs = 30;
  const ops = Array.from({ length: 150 }, (_, i) => ({ op: "set", tipo: "enviado", id: `0x${i}:0x${i}` }));
  const t = Date.now();
  const r = await corpo(await tratar(post({ lista: "sites", ops }), g));
  assert.equal(r.n, 150);
  assert.equal(Object.keys(await ler("sites", g)).length, 150);
  assert.ok(g.maximo <= 20 && g.maximo > 1, "paralelismo " + g.maximo);
  assert.ok(Date.now() - t < 1500, `150 marcas levaram ${Date.now() - t} ms`);
}
console.log("marcas.mjs: tudo certo");
