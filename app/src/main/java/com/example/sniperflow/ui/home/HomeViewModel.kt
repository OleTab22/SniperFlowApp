@file:Suppress("unused")
package com.example.sniperflow.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import com.example.sniperflow.ui.UiState

class HomeViewModel : ViewModel() {
    private val _state = MutableStateFlow(UiState(loading = true))
    val state: StateFlow<UiState> = _state

    fun refreshWithState(run: suspend () -> Unit) {
        _state.value = UiState(loading = true)
        viewModelScope.launch {
            try {
                run()
                _state.value = UiState(loading = false, empty = false)
            } catch (t: Throwable) {
                _state.value = UiState(loading = false, error = "Failed to refresh. Check network.")
            }
        }
    }
}


