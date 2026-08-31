# Theatre Business Bench v2 — campanha oficial terminal

> **Estado da alegação:** campanha oficial encerrada em falhas de contrato; resultado econômico não observado.

## Resultado correto

- **Resultado econômico:** não observado
- **Vencedor:** nenhum / não calculável
- **Seeds verificadas:** 5/5
- **Seeds terminais `failed_contract`:** 5
- **Seeds com `result.json`:** 0

Os placares parciais têm horizontes diferentes e não são agregados. Média,
mediana, vitórias por seed e bootstrap permanecem ausentes por desenho.

## Confiabilidade observada

- Gate disparado no controle: 2/5 seeds
- Gate disparado no Theatre: 3/5 seeds
- Chamadas: controle 51 · Theatre 78
- Tokens reportados: controle 3537628 · Theatre 2889993

| Seed | Braço/fase terminal | Dia controle / Theatre | Calls controle / Theatre | Tokens controle / Theatre |
|---:|---|---:|---:|---:|
| 2201 | `control` / `control` | 24 / 24 | 10 / 11 | 512086 / 318977 |
| 2202 | `theatre` / `actor` | 57 / 54 | 19 / 28 | 1920675 / 1382522 |
| 2203 | `theatre` / `actor` | 30 / 27 | 10 / 19 | 523695 / 607064 |
| 2204 | `theatre` / `actor` | 30 / 27 | 10 / 16 | 535985 / 507733 |
| 2205 | `control` / `control` | 3 / 3 | 2 / 4 | 45187 / 73697 |

## Causas preservadas

- **Seed 2201 · control/control:** historical invalid model JSON reconciled from OpenClaw trajectory
- **Seed 2202 · theatre/actor:** theatre handoff: actor did not execute every bound critical-correction item
- **Seed 2203 · theatre/actor:** theatre handoff: actor did not execute every bound critical-correction item
- **Seed 2204 · theatre/actor:** theatre handoff: actor did not execute every bound critical-correction item
- **Seed 2205 · control/control:** control: control plan omits a required critical-correction action type

## Integridade

- Replay: `passed` nas cinco seeds
- Arquivos confrontados: 168
- Manifesto da evidência: `84fb5d2465a470cd8b93101f6878b17d6d806cd9940dd521a8b65d12ea766823`
- Digest do relatório: `2ad8139a1831fb345ed6f613af906e19382ef710545ca66d61c9d2f03cdc3030`

## Próxima fronteira

As seeds 2201–2205 permanecem imutáveis e não podem ser retomadas ou recriadas.
Fechar a prova econômica exige outro protocolo pré-registrado e novas seeds; essa
decisão não altera nem transforma esta campanha terminal em resultado.

## Limites honestos

- Este relatório mede integridade e confiabilidade operacional da campanha, não desempenho econômico comparável.
- Dias, chamadas e tokens descrevem onde cada execução parou; não são scores finais nem podem ser agregados como se os horizontes fossem iguais.
- Atribuição do braço que disparou o gate descreve o contrato observado; não estabelece superioridade do outro braço.
- Qualquer protocolo futuro deve usar nova versão e novas seeds, preservando esta rodada sem edição, retomada ou descarte.
