plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    // Switch from kapt to KSP for Room
    alias(libs.plugins.google.ksp)
    id("com.google.gms.google-services")
}

android {
    namespace = "com.example.sniperflow"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.example.sniperflow"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        debug {
            buildConfigField("String", "BASE_URL", "\"https://sniperflow-api.onrender.com/\"")
        }
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            // Point to your production API endpoint (HTTPS)
            buildConfigField("String", "BASE_URL", "\"https://sniperflow-api.onrender.com/\"")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    kotlinOptions {
        jvmTarget = "11"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    lint {
        abortOnError = false
        warningsAsErrors = false
        disable += setOf(
            "HardcodedText",
            "RtlSymmetry",
            "RtlHardcoded"
        )
    }
}

dependencies {

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    // Firebase
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.analytics)
    implementation(libs.firebase.auth)
    // Encrypted storage (removed: using no encrypted prefs currently)
    // Coroutines Task await for Play Services/Firebase
    implementation(libs.kotlinx.coroutines.play.services)
    // Views and core libs
    implementation(libs.material)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.constraintlayout)
    // Coroutines
    implementation(libs.kotlinx.coroutines.android)
    // Networking
    implementation(libs.retrofit)
    implementation(libs.retrofit.moshi)
    implementation(libs.okhttp)
    implementation(libs.okhttp.logging)
    // JSON
    implementation(libs.moshi.kotlin)
    // Logging
    implementation(libs.timber)
    // Room (offline journal)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)
    // WorkManager for background sync
    implementation(libs.androidx.work.runtime.ktx)
    // Images (Photo thumbnails)
    implementation(libs.coil.kt)
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.ui.test.junit4)
    debugImplementation(libs.androidx.ui.tooling)
    debugImplementation(libs.androidx.ui.test.manifest)
    // Flexbox for driver chips
    implementation(libs.google.flexbox)
    // Pull-to-refresh
    implementation(libs.androidx.swiperefreshlayout)
    // NestedScrollView (already in core-ktx, ensure dependency present via libs)
    implementation(libs.androidx.core.ktx)
    // RecyclerView for alerts list
    implementation(libs.androidx.recyclerview)
    // Lifecycle process for app foreground/background callbacks (keep-alive)
    implementation(libs.androidx.lifecycle.process)
}