import Foundation
import Testing
@testable import agtermCore

@MainActor
final class WindowLibraryIndexTests {
    private let directory: URL
    private var indexURL: URL { directory.appendingPathComponent("windows.json") }

    init() throws {
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("agterm-index-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    deinit {
        try? FileManager.default.removeItem(at: directory)
    }

    private func blockIndexWrites() throws {
        try FileManager.default.removeItem(at: indexURL)
        try FileManager.default.createDirectory(at: indexURL, withIntermediateDirectories: true)
        try Data().write(to: indexURL.appendingPathComponent("occupied"))
    }

    private func savedWindowIDs() throws -> [UUID] {
        try JSONDecoder().decode(WindowsIndex.self, from: Data(contentsOf: indexURL)).windows.map(\.id)
    }

    @Test func aFailedIndexWriteIsRetriedByTheNextSnapshotSaveThatLands() throws {
        let library = WindowLibrary(directory: directory)
        let store = try #require(library.activeStore)
        #expect(!library.indexUnsaved)
        try blockIndexWrites()

        let added = library.newWindow(name: "second")
        #expect(library.indexUnsaved)
        #expect(store.saveChecked())
        #expect(library.indexUnsaved)

        try FileManager.default.removeItem(at: indexURL)
        store.save()

        #expect(!library.indexUnsaved)
        #expect(try savedWindowIDs().contains(added.id))
    }

    @Test func aSnapshotSaveLeavesACleanIndexAlone() throws {
        let library = WindowLibrary(directory: directory)
        let store = try #require(library.activeStore)
        try FileManager.default.removeItem(at: indexURL)

        store.save()

        #expect(!FileManager.default.fileExists(atPath: indexURL.path))
    }

    @Test func theCheckedSaveWritesTheIndexAndReportsItsFailure() throws {
        let library = WindowLibrary(directory: directory)
        let added = library.newWindow(name: "second")
        try FileManager.default.removeItem(at: indexURL)

        #expect(library.saveAllChecked())
        #expect(try savedWindowIDs().contains(added.id))

        try blockIndexWrites()
        #expect(!library.saveAllChecked())
        #expect(library.indexUnsaved)
    }

    @Test func theCheckedSaveStillWritesTheIndexWhenASnapshotFails() throws {
        let library = WindowLibrary(directory: directory)
        let added = library.newWindow(name: "second")
        let snapshot = directory.appendingPathComponent("windows/\(added.id.uuidString).json")
        try FileManager.default.removeItem(at: snapshot)
        try FileManager.default.createDirectory(at: snapshot, withIntermediateDirectories: true)
        try Data().write(to: snapshot.appendingPathComponent("occupied"))
        try FileManager.default.removeItem(at: indexURL)

        #expect(!library.saveAllChecked())

        #expect(try savedWindowIDs().contains(added.id))
        #expect(!library.indexUnsaved)
    }

    @Test func theTreeCarriesTheFlagItIsGivenAndOmitsItOtherwise() throws {
        let library = WindowLibrary(directory: directory)
        let store = try #require(library.activeStore)

        #expect(store.controlTree(paneForeground: { _ in nil }).indexUnsaved == nil)
        #expect(store.controlTree(paneForeground: { _ in nil }, indexUnsaved: true).indexUnsaved == true)
    }
}
