// SPDX-License-Identifier: Apache-2.0
package dev.simmirror.example

import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.ViewActions.click
import androidx.test.espresso.action.ViewActions.closeSoftKeyboard
import androidx.test.espresso.action.ViewActions.typeText
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.withId
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class MainActivityTest {
    @Test
    fun startsEmpty() {
        ActivityScenario.launch(MainActivity::class.java).use {
            onView(withId(R.id.list)).check(matches(withText("Nothing to buy")))
        }
    }

    @Test
    fun addsAnItem() {
        ActivityScenario.launch(MainActivity::class.java).use {
            onView(withId(R.id.item)).perform(typeText("Milk"), closeSoftKeyboard())
            onView(withId(R.id.add)).perform(click())
            onView(withId(R.id.list)).check(matches(withText("Milk")))
        }
    }
}
