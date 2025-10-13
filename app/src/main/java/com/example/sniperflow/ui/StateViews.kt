@file:Suppress("unused")
package com.example.sniperflow.ui

import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import com.example.sniperflow.R

data class UiState(val loading: Boolean = false, val error: String? = null, val empty: Boolean = false)

fun ViewGroup.bindState(state: UiState, onRetry: (() -> Unit)? = null) {
    val l = findViewById<View>(R.id.stateLoading)
    val e = findViewById<ViewGroup>(R.id.stateError)
    val m = findViewById<View>(R.id.stateEmpty)
    l?.visibility = if (state.loading) View.VISIBLE else View.GONE
    m?.visibility = if (state.empty) View.VISIBLE else View.GONE
    if (e != null) {
        e.visibility = if (state.error != null) View.VISIBLE else View.GONE
        e.findViewById<TextView>(R.id.txtErr)?.text = state.error ?: ""
        e.findViewById<Button>(R.id.btnRetry)?.setOnClickListener { onRetry?.invoke() }
    }
}


