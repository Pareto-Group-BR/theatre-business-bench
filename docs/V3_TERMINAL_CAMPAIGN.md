# Theatre Business Bench v3 — campanha oficial terminal

> **Estado da alegação:** campanha oficial terminal; efeito econômico não identificável.

## Resultado correto

- **Seeds verificadas:** 5/5
- **Seeds concluídas com resultado:** 0
- **Seeds `failed_contract`:** 5
- **Vencedor agregado:** nenhum / não calculável
- **Agregado econômico:** não calculável
- **Motivo:** nenhuma seed produziu result.json; placares parciais não são resultados

## Confiabilidade e custo observados

- Gate terminal no controle: 3
- Gate terminal no Theatre: 2
- Chamadas: controle 218 · Theatre 370
- Reparos aceitos: controle 1 · Theatre 5

| Seed | Estado terminal | Dia controle / Theatre | Calls controle / Theatre | Tokens controle / Theatre |
|---:|---|---:|---:|---:|
| 2301 | `control/control` | 72 / 72 | 26 / 42 | 3113028 / 2579531 |
| 2302 | `theatre/actor` | 96 / 93 | 32 / 54 | 3346868 / 3396969 |
| 2303 | `control/control` | 138 / 138 | 46 / 77 | 6308206 / 7725690 |
| 2304 | `theatre/actor` | 99 / 96 | 34 / 59 | 4341829 / 5027924 |
| 2305 | `control/control` | 240 / 240 | 80 / 138 | 11967190 / 16429127 |

## Causas terminais preservadas

- **Seed 2301 · control/control:** gateway restarted during the frozen repair; auto-continuation preserved and charged but not applied
- **Seed 2302 · theatre/actor:** gateway restart left a write-ahead attempt with no OpenClaw dispatch observed; terminalized without retry
- **Seed 2303 · control/control:** OpenClaw model call failed (1): GatewayClientRequestError: Error: CLI transcript compaction failed for openai/gpt-5.6-sol: Summarization failed: terminated
- **Seed 2304 · theatre/actor:** gateway restarted during the frozen original invocation; auto-continuation preserved and charged but not applied
- **Seed 2305 · control/control:** OpenClaw model call failed (1): GatewayClientRequestError: Error: CLI transcript compaction failed for openai/gpt-5.6-sol: Summarization failed: Our servers are currently overloaded. Please try again later.

## Integridade

- Replay: `passed` nas cinco seeds
- Arquivos confrontados: 201
- Manifesto da evidência: `e6c34c46273ed6166de95ad497e5df7a769b3725e57d0571c39c91d7f1df7d72`
- Digest do relatório: `bf4947b49d97522bfb49a9f8e84ee96bfd91f28aeafd53e825578d719538ec69`

## Limites honestos

- Falhas terminais são evidência de confiabilidade do tratamento e nunca recebem o score parcial do ponto onde pararam.
- Dias, chamadas e tokens de pares falhos não são comparados como se os horizontes fossem iguais.
- Reparos mostram robustez contratual e custo; não substituem o resultado econômico anual.
- O benchmark econômico complementa, mas não substitui, o júri humano cego do épico Theatre.
