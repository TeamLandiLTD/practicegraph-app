//
//  MenuBarIcon.swift
//  The menu-bar mark, drawn programmatically.
//
//  macOS menu-bar icons are template images: monochrome shapes the system
//  tints for light/dark menu bars and selection. So the base icon here is the
//  brand mark reduced to a single-color silhouette (three rising bars in a
//  rounded square) rendered as a template — NOT the full-color mark, which
//  would look wrong when the menu bar inverts.
//
//  The "working" variant adds a small green presence dot, mirroring
//  packaging/make_icon.py's green-dot icon and the Windows tray's
//  practicegraph-working.ico. That dot is intentionally colored (a status
//  signal), so the working image is drawn NON-template with the dot in the
//  design system's positive green; the bars stay near-foreground so they read
//  in both appearances.
//
//  Drawing it in code keeps a single source of truth for the mark's geometry
//  and avoids shipping a raster that could drift from assets/mark.svg.
//

import AppKit

enum MenuBarIcon {

    // Design-system tokens (mirror make_icon.py).
    private static let positiveGreen = NSColor(
        srgbRed: 0x2f / 255.0, green: 0x7d / 255.0, blue: 0x57 / 255.0, alpha: 1.0)

    /// Menu-bar image at the standard ~18pt status height.
    static func image(working: Bool) -> NSImage {
        let side: CGFloat = 18
        let image = NSImage(size: NSSize(width: side, height: side), flipped: false) { rect in
            drawMark(in: rect, working: working)
            return true
        }
        // Base (quiet) icon is a template so the system tints it; the working
        // icon carries a real color (the green dot) and must not be tinted.
        image.isTemplate = !working
        return image
    }

    private static func drawMark(in rect: NSRect, working: Bool) {
        let foreground = NSColor.labelColor // becomes the template stencil color

        // Three rising bars, matching the mark's proportions. We draw only the
        // bars (not the filled rounded square) so the template silhouette stays
        // legible at menu-bar size — a solid square would read as a blob.
        let inset = rect.width * 0.14
        let content = rect.insetBy(dx: inset, dy: inset)
        let barCount = 3
        let gap = content.width * 0.14
        let barWidth = (content.width - gap * CGFloat(barCount - 1)) / CGFloat(barCount)
        let heights: [CGFloat] = [0.45, 0.72, 1.0]

        foreground.setFill()
        for index in 0..<barCount {
            let h = content.height * heights[index]
            let x = content.minX + CGFloat(index) * (barWidth + gap)
            let barRect = NSRect(x: x, y: content.minY, width: barWidth, height: h)
            let path = NSBezierPath(
                roundedRect: barRect,
                xRadius: barWidth * 0.28,
                yRadius: barWidth * 0.28
            )
            path.fill()
        }

        if working {
            // Presence dot, top-right, on a small paper ring so it stays
            // visible over a dark menu bar.
            let dotDiameter = rect.width * 0.42
            let dotRect = NSRect(
                x: rect.maxX - dotDiameter,
                y: rect.maxY - dotDiameter,
                width: dotDiameter,
                height: dotDiameter
            )
            NSColor.windowBackgroundColor.setFill()
            NSBezierPath(ovalIn: dotRect.insetBy(dx: -1, dy: -1)).fill()
            positiveGreen.setFill()
            NSBezierPath(ovalIn: dotRect).fill()
        }
    }
}
