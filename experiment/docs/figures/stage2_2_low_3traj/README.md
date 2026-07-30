# Stage 2.2 Low-reasoning Three-trajectory Figures

`traj_002`, `traj_003`, `traj_010`의 checkpoints 60, 120, 180, 240, 300을
trajectory별로 표시하고, 각 checkpoint에서 세 trajectory를 동일 가중한
macro average를 함께 제공한다.

| Version directory | Source plans |
|---|---|
| `low_3traj__71ea1d01981a__9bb8ed9ee53c__460647a3a1bc/` | `traj_002=71ea1d01981a06ed6906fdc930d36a1cdcfa5b17efce04385d0619057ab3949a`, `traj_003=9bb8ed9ee53cbc54a4d9e3ead07204675c07cc784ab2558d75c9cd10b6e7c515`, `traj_010=460647a3a1bcc38382800ba2e2be6114439c4d2b7f9c09e21d79296f51542bfd` |

각 version directory는 source CSV와 SVG/PNG/PDF를 포함한다. Gemini
`traj_002`, checkpoint 240의 invalid JSON은 규약대로 0점으로 유지하고 그래프에
parse failure로 표시한다.
