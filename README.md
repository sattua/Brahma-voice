# Brahma

Una persona cuenta una historia. El programa la parte en páginas y dibuja la novela gráfica de forma que el mismo personaje siga siendo el mismo de viñeta en viñeta. Cada página se entrega como [OpenRaster](https://www.openraster.org/) (`.ora`): capas de verdad, editables en Krita, GIMP, MyPaint y Drawpile.

El dibujo sale de lo aprendido en `graphic-harness`. Allí una imagen de referencia manda más que un párrafo largo, y en esta máquina (RX 9070 XT de 16 GB, 32 GB de RAM) SDXL con el muestreador ya medido cabe y es repetible. Brahma usa esa misma idea para una historia, no para un objeto 3D.

## Consistencia

Hay tres cerraduras. La viñeta anterior no es una de ellas.

1. **Estilo.** Una sola frase (medio, línea, color, luz) y un solo muestreador. Checkpoint, pasos, CFG, sampler y scheduler son los mismos en todas las imágenes. Cambiar el sampler es cambiar de dibujante.
2. **Identidad.** Cada personaje tiene una hoja de modelo y cada lugar una placa. Se generan una vez, en PNG, y se pueden repintar. Una viñeta mira **una** de esas imágenes: la hoja del personaje principal, o la placa del lugar si no hay nadie. El peso es 0.85 para la cara y la ropa, 0.55 para el lugar.
3. **Plano.** El encuadre sale de una lista cerrada (`close`, `medium`, `wide`, `establishing`, …). Así cambia la cámara sin reescribir a la persona.

La cláusula visual del personaje es la misma cadena en la hoja y en cada viñeta. El diálogo se queda en el idioma de quien contó la historia; las descripciones que ve el modelo de imagen van en inglés.

No se usa la viñeta anterior como referencia fuerte. Copiar el cuadro previo repite la pose y aun así deja que la cara se vaya. La identidad viene de la hoja, el lugar de la placa, la acción del guion.

En una GPU de 16 GB entra **un** IP-Adapter. Un segundo adaptador de estilo pelea con el primero y se queda sin memoria. El estilo ya está dentro de la hoja, porque la hoja se generó con la cerradura de estilo. Las hojas son texto a imagen. Solo las viñetas condicionan en píxeles.

## Qué modelo

El guion y el dibujo no comparten modelo, y no corren a la vez. Es la misma disciplina que la cola de GPU del harness: una cosa usa la tarjeta.

**Guion: qwen3:8b** en Ollama (`http://127.0.0.1:11434/v1`), con el pensamiento apagado para que devuelva el JSON directo. Es el Qwen que ya está en esta máquina: cabe, sigue el esquema (reparto, lugares, páginas, planos) y no sostiene la GPU mientras se dibuja. Un 70B no cabe junto a esta tarjeta y 32 GB de RAM. El contrato es el JSON; otro modelo se cambia en `script.model`.

**Imagen: la herramienta de imagen de Cursor.** ComfyUI no está en esta máquina. El servidor no dibuja: devuelve un job con la descripción cerrada, el aspecto y `reference_image_paths`. El agente genera esa imagen y devuelve el archivo. La hoja del personaje es la referencia de cada viñeta suya; la placa, la de los planos vacíos.

Cuando haya un ComfyUI en el puerto 8188, `image.backend` pasa a `comfyui`. Ahí el dibujo es SDXL (30 pasos, CFG 6.5, `dpmpp_2m`, `karras`, lado largo 1024) y, con el paquete IP-Adapter, `workflow: "sdxl_ipadapter"` condiciona en la misma hoja. Un solo adaptador: en 16 GB un segundo se queda sin memoria.

## Ediciones baratas y caras

Igual que el harness elige el camino más barato:

| Qué cambió | Qué se vuelve a hacer |
| --- | --- |
| Un globo, una frase | `novel_assemble` con `dialogue`. No se redibuja. |
| Una viñeta | `novel_draw` con `panel`, por ejemplo `p01_02`, y una imagen nueva. |
| La cara o el abrigo | Una hoja nueva en `novel_refs` y después las viñetas de ese personaje. |
| El estilo | Hoja de estilo, hojas y placas nuevas, y todas las viñetas. |

`pages/` se regenera. El original es `project.json` (texto) y los PNG de `panels/` y `refs/`. Si se va a retocar el `.ora` a mano y después se va a reensamblar, se guarda una copia.

## Formato de página

Página de 6×9 pulgadas a 300 dpi (1800×2700). Cada viñeta es un grupo con dos capas: `art` y `lettering`. Debajo, `frames` y `paper`. Los globos no se hornean en el dibujo. El PNG suelto de cada viñeta, a tamaño de generación, queda en `panels/` por si se quiere sustituir el arte y reensamblar.

Límites de producción, por tiempo de GPU: 8 páginas, 6 viñetas, 4 personajes, 4 lugares.

## Uso desde Cursor

El servidor MCP `brahma` ya está registrado. No hay comandos que lanzar. En el chat se cuenta la historia y el agente usa las herramientas: `novel_plan`, `novel_settings`, `novel_refs`, `novel_draw`, `novel_assemble`.

Una imagen adjunta puede ser referencia gráfica (`role: style`, `character` o `location`): se copia a `references/` y viaja en `reference_image_paths`. Si es un dibujo a mano (`role: sketch`), el chat lo mira y esa lectura (medio, paleta, trazo, notas) se escribe en `settings.json`. El archivo del dibujo sigue siendo referencia. No hay un modelo de visión local; si la lectura no viene en la llamada, la herramienta responde `needs_reading` y se completa con `novel_settings`.

En esta máquina el guion lo escribe **qwen3:8b** en Ollama, que es el Qwen que ya está instalado. Los píxeles los pide el agente a la herramienta de imagen de Cursor. Cada viñeta recibe la hoja del personaje en `reference_image_paths`; esa ruta es la cerradura de identidad. ComfyUI no está instalado, así que el flujo `sdxl_ipadapter` queda para cuando exista un servidor en el puerto 8188 (`image.backend = "comfyui"`).

```text
novel_plan → dibujar cada job de referencia → novel_refs con los archivos
→ novel_draw → dibujar cada viñeta con su referencia → novel_draw con los archivos
→ novel_assemble
```

```text
workspace/<historia>/
  settings.json              # tipo de arte, paleta, línea, luz y las imágenes de referencia
  references/                # dibujos y referencias subidas
  project.json
  story.txt
  refs/style.png
  refs/characters/<id>.png
  refs/locations/<id>.png
  panels/p01_01.png          # arte a resolución de generación
  panels/p01_01.json         # prompt, semilla, referencia y sampler usados
  pages/page-01.ora          # capas, para Krita o GIMP
  pages/page-01.png          # vista plana
```

## Pruebas

```bash
.venv/bin/python -m pytest -q
```

No hace falta GPU. Las pruebas comprueban que la cláusula del personaje es la misma en la hoja y en la viñeta, que ninguna viñeta referencia a la anterior, que el `.ora` abre como capas separadas, y que cambiar un diálogo no vuelve a dibujar.
