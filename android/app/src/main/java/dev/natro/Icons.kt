package dev.natro

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** Natro's icons: rounded lines on a 24-unit grid, drawn rather than loaded, so they match at any size. */
enum class Glyph { Send, Back, Settings, Chevron, Close, Check, Power, Mic }

@Composable
fun Icon(glyph: Glyph, color: Color, modifier: Modifier = Modifier, size: Dp = 24.dp, weight: Dp = 2.dp) {
    Canvas(modifier.size(size)) {
        val unit = this.size.minDimension / 24f
        val stroke = Stroke(weight.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round)
        fun at(x: Float, y: Float) = Offset(x * unit, y * unit)
        fun line(vararg points: Float) {
            val path = Path().apply {
                moveTo(points[0] * unit, points[1] * unit)
                for (i in 2 until points.size step 2) lineTo(points[i] * unit, points[i + 1] * unit)
            }
            drawPath(path, color, style = stroke)
        }
        when (glyph) {
            Glyph.Send -> {
                line(12f, 19f, 12f, 5f)
                line(6f, 11f, 12f, 5f, 18f, 11f)
            }
            Glyph.Back -> {
                line(19f, 12f, 5f, 12f)
                line(11f, 6f, 5f, 12f, 11f, 18f)
            }
            Glyph.Settings -> sliders(unit, color, stroke)
            Glyph.Chevron -> line(6f, 9.5f, 12f, 15.5f, 18f, 9.5f)
            Glyph.Close -> {
                line(6.5f, 6.5f, 17.5f, 17.5f)
                line(17.5f, 6.5f, 6.5f, 17.5f)
            }
            Glyph.Check -> line(5f, 12.5f, 10f, 17.5f, 19f, 7f)
            Glyph.Power -> {
                drawArc(color, startAngle = -60f, sweepAngle = 300f, useCenter = false, topLeft = at(5f, 6f),
                    size = Size(14f * unit, 14f * unit), style = stroke)
                line(12f, 3.5f, 12f, 11f)
            }
            Glyph.Mic -> {
                drawRoundRect(color, topLeft = at(9f, 3f), size = Size(6f * unit, 11f * unit),
                    cornerRadius = CornerRadius(3f * unit), style = stroke)
                drawArc(color, startAngle = 0f, sweepAngle = 180f, useCenter = false, topLeft = at(5.5f, 4.5f),
                    size = Size(13f * unit, 13f * unit), style = stroke)
                line(12f, 17.5f, 12f, 21f)
            }
        }
    }
}

/** Three sliders, for settings. */
private fun DrawScope.sliders(unit: Float, color: Color, stroke: Stroke) {
    for ((y, knob) in listOf(7f to 15f, 12f to 8.5f, 17f to 13f)) {
        val radius = 2.2f
        drawLine(color, Offset(4f * unit, y * unit), Offset((knob - radius - 1f) * unit, y * unit), stroke.width, StrokeCap.Round)
        drawLine(color, Offset((knob + radius + 1f) * unit, y * unit), Offset(20f * unit, y * unit), stroke.width, StrokeCap.Round)
        drawCircle(color, radius * unit, Offset(knob * unit, y * unit), style = stroke)
    }
}
