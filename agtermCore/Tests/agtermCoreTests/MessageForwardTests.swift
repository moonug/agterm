import Foundation
import Testing
@testable import agtermCore

struct MessageForwardTests {
    @Test func sendRebuildsFlagsAndBodyOperand() throws {
        let request = ControlRequest(cmd: .msgSend, args: ControlArgs(
            workspace: "w1", body: "hello", recipients: ["S1:P1", "S2:P2"], from: "S3:P3"))

        let (words, operands) = try #require(MessageForward.arguments(for: request), "a complete send forwards")

        #expect(words == ["msg", "send", "--to", "S1:P1", "--to", "S2:P2", "--workspace", "w1", "--from", "S3:P3"])
        #expect(operands == ["hello"])
    }

    @Test func readAndAckCarryIdentityAndOperands() throws {
        let read = try #require(MessageForward.arguments(for:
            ControlRequest(cmd: .msgRead, args: ControlArgs(identity: "S:P", ack: true))), "a read forwards")
        let ack = try #require(MessageForward.arguments(for:
            ControlRequest(cmd: .msgAck, args: ControlArgs(identity: "S:P", ids: ["m1", "m2"]))), "an ack forwards")

        #expect(read.words == ["msg", "read", "--as", "S:P", "--ack"])
        #expect(read.operands == [])
        #expect(ack.words == ["msg", "ack", "--as", "S:P"])
        #expect(ack.operands == ["m1", "m2"])
    }

    @Test func incompleteRequestsAreNotForwarded() {
        #expect(MessageForward.arguments(for: ControlRequest(cmd: .msgSend, args: ControlArgs(recipients: ["S:P"]))) == nil)
        #expect(MessageForward.arguments(for: ControlRequest(cmd: .msgRead, args: ControlArgs())) == nil)
        #expect(MessageForward.arguments(for: ControlRequest(cmd: .msgAck, args: ControlArgs(identity: "S:P"))) == nil)
        #expect(MessageForward.arguments(for: ControlRequest(cmd: .sessionGo)) == nil)
    }

    @Test func onlyCommandsThatMayHaveTakenEffectCarryANoAnswerNote() {
        #expect(MessageForward.noAnswerNote(for: ControlRequest(cmd: .msgSend, args: ControlArgs(body: "x"))) != nil)
        #expect(MessageForward.noAnswerNote(for: ControlRequest(cmd: .msgRead, args: ControlArgs(identity: "S:P", ack: true))) != nil)
        #expect(MessageForward.noAnswerNote(for: ControlRequest(cmd: .msgRead, args: ControlArgs(identity: "S:P"))) == nil)
        #expect(MessageForward.noAnswerNote(for: ControlRequest(cmd: .msgAck, args: ControlArgs(identity: "S:P", ids: ["m1"]))) == nil)
    }

    @Test func anUnknownOutcomeReplyCarriesTheStablePrefix() {
        let reply = ControlResponse.unknownOutcome("the Mac gave no answer; the message may have arrived, do not resend it")
        #expect(!reply.ok)
        #expect(reply.isUnknownOutcome())
        #expect(!ControlResponse(ok: false, error: "the Mac did not answer the message command").isUnknownOutcome())
        #expect(!ControlResponse(ok: true).isUnknownOutcome())
    }
}
