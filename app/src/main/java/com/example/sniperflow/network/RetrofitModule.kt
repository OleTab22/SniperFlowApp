package com.example.sniperflow.network

import android.os.Build
import androidx.annotation.RequiresApi
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.moshi.MoshiConverterFactory
import java.time.Duration
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory

object RetrofitModule {
    @RequiresApi(Build.VERSION_CODES.O)
    fun api(baseUrl: String): BrokerApi {
        val client = OkHttpClient.Builder()
            .callTimeout(Duration.ofSeconds(15))
            .connectTimeout(Duration.ofSeconds(10))
            .readTimeout(Duration.ofSeconds(15))
            .build()
        val moshi = Moshi.Builder()
            .add(KotlinJsonAdapterFactory())
            .build()
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .addConverterFactory(MoshiConverterFactory.create(moshi))
            .client(client)
            .build()
            .create(BrokerApi::class.java)
    }
}


