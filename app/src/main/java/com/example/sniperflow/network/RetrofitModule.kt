package com.example.sniperflow.network

import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.moshi.MoshiConverterFactory
import java.time.Duration

object RetrofitModule {
    fun api(baseUrl: String): BrokerApi {
        val client = OkHttpClient.Builder()
            .callTimeout(Duration.ofSeconds(15))
            .connectTimeout(Duration.ofSeconds(10))
            .readTimeout(Duration.ofSeconds(15))
            .build()
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .addConverterFactory(MoshiConverterFactory.create())
            .client(client)
            .build()
            .create(BrokerApi::class.java)
    }
}


