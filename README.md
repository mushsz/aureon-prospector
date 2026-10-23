# Leads na nuvem

Busca leads sozinha, sem o computador ligado, e mantém o site de listas atualizado.

- **Rodada completa às 7h (Brasília)** e **vigia às 10h30, 14h30 e 18h30** (`.github/workflows/rodada.yml`).
  A rodada busca licitações (PNCP), hospitais (CNES) e empresas no Google Maps, gera as duas listas e grava aqui.
- **A Netlify publica o site** a cada gravação (`netlify.toml`): `publicar/p/sites.html` e `publicar/p/equipamentos.html`,
  atrás do endereço único `/`, mais a função das marcas compartilhadas (`netlify/functions/marcas.mjs`).
- **Reposição:** quando uma lista tem 50 leads ou menos sem ver, a rodada faz 60 pesquisas em vez de 15.
  A conta usa as marcas de "enviado" e "visto" do site (`repor.py`).
- Se o Google pedir verificação de robô, a coleta para e não tenta contornar; a próxima rodada continua do mesmo ponto.
- **Registro:** em Actions, abra a execução e baixe `rodada-log`. O estado fica nos arquivos `sites/dados/` e `hospitalar/dados/`.

`teste-maps.yml` é um teste manual: confere se o Google responde para a nuvem (Chrome e scraper kit).

Plano grátis do GitHub: 2.000 minutos por mês em repositório privado. A rodada completa leva uns 40 minutos.
