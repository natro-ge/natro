package dev.natro

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Where Natro works for what's asked on the phone: the brain's names for the devices, and the status line. */
class WorksOnTest {
    @Test
    fun onlyHisTwoDevicesAreNamed() {
        assertEquals("phone", WorksOn.named("phone"))
        assertEquals("pc", WorksOn.named("pc"))
        assertNull(WorksOn.named("tablet"))
        assertNull(WorksOn.named(null))
    }

    @Test
    fun theStatusLineSaysWhenSheWorksOnThePc() {
        assertNull(WorksOn.status(WorksOn.PHONE, pcOnline = true))
        assertEquals("works on your PC", WorksOn.status(WorksOn.PC, pcOnline = true))
        assertEquals("PC offline, so works here", WorksOn.status(WorksOn.PC, pcOnline = false))
    }
}
