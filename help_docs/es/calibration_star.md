# Estrella de calibración

Antes de guiar, PHD2 y ASIAIR necesitan **calibrar** los ejes de la cámara guía moviendo la montura en RA y Dec y midiendo el movimiento estelar resultante. Para la calibración más precisa, su estrella guía debe cumplir dos criterios:

1. **Cerca del ecuador celeste**: Declinación dentro de **±20°** alrededor de 0°, donde los movimientos de RA/Dec son más ortogonales
2. **Cerca del meridiano**: Ángulo horario dentro de **±1,5 horas**, lo que minimiza el error de cono y el juego de declinación de la montura

## Cómo funciona este widget

Este widget encuentra la **mejor estrella brillante disponible** que satisface ambos criterios para su ubicación y la fecha seleccionada, luego muestra la ventana de tiempo durante la cual esa estrella está en la zona de calibración utilizable.

## Uso

1. Establezca la **fecha** en la pestaña Gráfico para su noche de observación planificada
2. El widget muestra la estrella de calibración recomendada con sus **coordenadas RA/Dec**
3. La **ventana de calibración** muestra cuándo la estrella está dentro de la zona óptima
4. En ASIAIR, **apunte a la RA/Dec mostrada** antes de iniciar la calibración del guiado
5. Complete la calibración, luego vuelva a apuntar a su objetivo de imagen

## Consejos

- Si no se encuentra ninguna estrella, intente seleccionar una fecha diferente o verifique que su ubicación esté configurada correctamente
- El botón de actualización (↻) vuelve a ejecutar la búsqueda de estrellas para la fecha actual
- Las estrellas más brillantes (magnitud más baja) son preferidas para una detección más confiable de la estrella guía
