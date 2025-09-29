package com.example.sniperflow.auth

import android.content.Context
import com.google.firebase.auth.FirebaseAuth
import kotlinx.coroutines.tasks.await

class AuthRepository(private val ctx: Context) {
    private val auth: FirebaseAuth = FirebaseAuth.getInstance()
    private val prefs by lazy { SecurePrefs.instance(ctx) }

    suspend fun register(email: String, password: String) {
        auth.createUserWithEmailAndPassword(email, password).await()
    }

    suspend fun login(email: String, password: String) {
        auth.signInWithEmailAndPassword(email, password).await()
        val token = auth.currentUser?.getIdToken(true)?.await()?.token ?: ""
        prefs.edit().putString("idToken", token).apply()
    }

    fun currentUser() = auth.currentUser

    fun logout() {
        auth.signOut()
        prefs.edit().clear().apply()
    }

    suspend fun sendPasswordReset(email: String) {
        auth.sendPasswordResetEmail(email).await()
    }
}


