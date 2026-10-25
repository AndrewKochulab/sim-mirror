// SPDX-License-Identifier: Apache-2.0
package dev.simmirror.example

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView

/** A grocery list: type an item, press Add, and it is on the list. */
class MainActivity : Activity() {
    private val items = mutableListOf<String>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.main)
        val field = findViewById<EditText>(R.id.item)
        val list = findViewById<TextView>(R.id.list)
        // `sim_app launch` with ``item=Milk`` starts the list with it: launch arguments are intent extras.
        intent.getStringExtra("item")?.let { items.add(it) }
        show(list)
        findViewById<Button>(R.id.add).setOnClickListener {
            val item = field.text.toString().trim()
            if (item.isNotEmpty()) {
                items.add(item)
                field.text.clear()
                show(list)
            }
        }
    }

    private fun show(list: TextView) {
        list.text = if (items.isEmpty()) "Nothing to buy" else items.joinToString("\n")
    }
}
