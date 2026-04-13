pluginManagement {
    repositories {
        // Chaquopy is hosted at its own Maven repository
        maven { url = uri("https://chaquo.com/maven") }
        gradlePluginPortal()
        google()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        maven { url = uri("https://chaquo.com/maven-public") }
        google()
        mavenCentral()
    }
}

rootProject.name = "EufySync"
include(":app")
